"""Every way an upstream request can fail, and what the caller sees for each.

This matrix is the reason PR 2 exists: once these mappings hold, no tool has to
think about HTTP again.
"""

from collections.abc import Iterator

import anyio
import httpx
import pytest
import respx

from ihm_mcp.config import Settings
from ihm_mcp.errors import (
    RateLimitedError,
    UpstreamNotFound,
    UpstreamSchemaChangedError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from ihm_mcp.ihm_client import UpstreamClient

BASE_URL = "https://upstream.test"


def make_settings(**overrides) -> Settings:
    """Explicit settings — never whatever IHM_* the developer's shell holds."""
    values = {
        "base_url": BASE_URL,
        "request_timeout_seconds": 1.0,
        "cache_ttl_seconds": 300,
        "cache_max_entries": 512,
        "max_concurrent_requests": 4,
    }
    values.update(overrides)
    return Settings(**values)


class FakeClock:
    """Lets a cache TTL expire without a test that sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def client(clock: FakeClock) -> Iterator[UpstreamClient]:
    # No backoff sleep: the retry *policy* is what is under test, not the wait.
    yield UpstreamClient(make_settings(), retry_backoff_seconds=0, clock=clock)


@pytest.fixture
def mock_upstream() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


# --- the happy path ---------------------------------------------------------


async def test_get_json_parses_the_body_and_identifies_this_client(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    route = mock_upstream.get("/api/search/haifa").mock(
        return_value=httpx.Response(200, json=[{"title": "Haifa"}])
    )

    assert await client.get_json("/api/search/haifa") == [{"title": "Haifa"}]

    request = route.calls.last.request
    assert "israel-hiking-mcp/" in request.headers["user-agent"]
    assert request.headers["accept"] == "application/json"


async def test_get_bytes_returns_the_raw_body(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    tile = "/vector/data/global_points/12/2455/1662.mvt"
    route = mock_upstream.get(tile).mock(
        return_value=httpx.Response(
            200, content=b"\x1a\x0f", headers={"content-type": "application/x-protobuf"}
        )
    )

    assert await client.get_bytes(tile, accept="application/x-protobuf") == b"\x1a\x0f"
    assert route.calls.last.request.headers["accept"] == "application/x-protobuf"


# --- failure mapping --------------------------------------------------------


async def test_404_is_an_internal_signal_and_is_not_retried(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    """Only the calling tool knows whether a 404 is a missing place or route."""
    route = mock_upstream.get("/api/urls/missing").mock(return_value=httpx.Response(404))

    with pytest.raises(UpstreamNotFound):
        await client.get_json("/api/urls/missing")

    assert route.call_count == 1


async def test_429_is_reported_as_rate_limited_with_the_wait(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    route = mock_upstream.get("/api/search/x").mock(
        return_value=httpx.Response(429, headers={"retry-after": "30"})
    )

    with pytest.raises(RateLimitedError) as caught:
        await client.get_json("/api/search/x")

    assert "Retry after 30s" in str(caught.value)
    assert route.call_count == 1  # backing off is the caller's job, not a retry


async def test_other_4xx_is_treated_as_a_contract_mismatch(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    route = mock_upstream.get("/api/search/x").mock(return_value=httpx.Response(403))

    with pytest.raises(UpstreamSchemaChangedError):
        await client.get_json("/api/search/x")

    assert route.call_count == 1


async def test_5xx_is_retried_exactly_once(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    route = mock_upstream.get("/api/search/x").mock(return_value=httpx.Response(500))

    with pytest.raises(UpstreamUnavailableError) as caught:
        await client.get_json("/api/search/x")

    assert route.call_count == 2
    assert "500" in str(caught.value)


async def test_a_failure_that_clears_on_the_retry_succeeds(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    route = mock_upstream.get("/api/search/x").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": True})]
    )

    assert await client.get_json("/api/search/x") == {"ok": True}
    assert route.call_count == 2


async def test_timeouts_are_retried_then_reported_as_timeouts(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    route = mock_upstream.get("/api/search/x").mock(side_effect=httpx.ReadTimeout)

    with pytest.raises(UpstreamTimeoutError) as caught:
        await client.get_json("/api/search/x")

    assert route.call_count == 2
    assert "1s" in str(caught.value)


async def test_connection_failures_are_reported_as_unavailable(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    route = mock_upstream.get("/api/search/x").mock(side_effect=httpx.ConnectError)

    with pytest.raises(UpstreamUnavailableError):
        await client.get_json("/api/search/x")

    assert route.call_count == 2


async def test_a_non_json_body_is_a_schema_change(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    mock_upstream.get("/api/search/x").mock(
        return_value=httpx.Response(200, html="<html>maintenance</html>")
    )

    with pytest.raises(UpstreamSchemaChangedError):
        await client.get_json("/api/search/x")


async def test_truncated_json_is_a_schema_change(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    mock_upstream.get("/api/search/x").mock(
        return_value=httpx.Response(
            200, content=b'[{"title": "Hai', headers={"content-type": "application/json"}
        )
    )

    with pytest.raises(UpstreamSchemaChangedError):
        await client.get_json("/api/search/x")


async def test_upstream_bodies_and_urls_never_reach_the_message(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    """Error text goes straight into a model's context — it must stay clean."""
    body = "<html><body>Internal Server Error at /var/www/app.rb:42</body></html>"
    mock_upstream.get("/api/urls/secret-share-id").mock(
        return_value=httpx.Response(500, html=body)
    )

    with pytest.raises(UpstreamUnavailableError) as caught:
        await client.get_json("/api/urls/secret-share-id")

    message = str(caught.value)
    assert "<html>" not in message
    assert "/var/www" not in message
    assert "\n" not in message


# --- caching ----------------------------------------------------------------


async def test_a_repeated_request_is_served_from_cache(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    route = mock_upstream.get("/api/search/haifa").mock(
        return_value=httpx.Response(200, json=["haifa"])
    )

    assert await client.get_json("/api/search/haifa") == ["haifa"]
    assert await client.get_json("/api/search/haifa") == ["haifa"]

    assert route.call_count == 1


async def test_cache_entries_expire(
    client: UpstreamClient, mock_upstream: respx.MockRouter, clock: FakeClock
):
    route = mock_upstream.get("/api/search/haifa").mock(
        return_value=httpx.Response(200, json=["haifa"])
    )

    await client.get_json("/api/search/haifa")
    clock.advance(301)
    await client.get_json("/api/search/haifa")

    assert route.call_count == 2


async def test_parameters_are_part_of_the_cache_key_but_their_order_is_not(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    route = mock_upstream.get("/api/routing").mock(
        return_value=httpx.Response(200, json={"type": "FeatureCollection"})
    )

    await client.get_json("/api/routing", params={"from": "32,35", "type": "Hike"})
    await client.get_json("/api/routing", params={"type": "Hike", "from": "32,35"})
    assert route.call_count == 1  # same request, written differently

    await client.get_json("/api/routing", params={"from": "32,35", "type": "Bike"})
    assert route.call_count == 2  # genuinely different request


async def test_failures_are_never_cached(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    """A cached 500 would poison the rest of the session."""
    route = mock_upstream.get("/api/search/x").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(200, json=["found"]),
        ]
    )

    with pytest.raises(UpstreamUnavailableError):
        await client.get_json("/api/search/x")
    assert await client.get_json("/api/search/x") == ["found"]
    assert route.call_count == 3


async def test_the_cache_is_bounded(mock_upstream: respx.MockRouter, clock: FakeClock):
    """A hundred-tile fan-out must not grow the cache without limit."""
    client = UpstreamClient(
        make_settings(cache_max_entries=2), retry_backoff_seconds=0, clock=clock
    )
    for index in range(3):
        mock_upstream.get(f"/tile/{index}").mock(
            return_value=httpx.Response(200, json=index)
        )
        await client.get_json(f"/tile/{index}")

    # The oldest entry was evicted, so asking for it again goes upstream.
    assert await client.get_json("/tile/0") == 0
    assert mock_upstream.calls.call_count == 4


async def test_ttl_zero_disables_caching(mock_upstream: respx.MockRouter):
    client = UpstreamClient(make_settings(cache_ttl_seconds=0), retry_backoff_seconds=0)
    route = mock_upstream.get("/api/search/x").mock(
        return_value=httpx.Response(200, json=["x"])
    )

    await client.get_json("/api/search/x")
    await client.get_json("/api/search/x")

    assert route.call_count == 2


# --- concurrency ------------------------------------------------------------


async def test_concurrent_requests_stay_under_the_configured_cap(
    mock_upstream: respx.MockRouter,
):
    """The cap is a promise to a volunteer-run host; it has to actually hold."""
    settings = make_settings(max_concurrent_requests=2)
    client = UpstreamClient(settings, retry_backoff_seconds=0)

    in_flight = 0
    peak = 0

    async def slow_response(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await anyio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, json={"path": request.url.path})

    mock_upstream.get(path__startswith="/tile/").mock(side_effect=slow_response)

    async with anyio.create_task_group() as tasks:
        for index in range(12):
            tasks.start_soon(client.get_json, f"/tile/{index}")

    assert mock_upstream.calls.call_count == 12
    assert peak <= settings.max_concurrent_requests
