"""`search_places`, end to end over the MCP boundary.

The fixtures below are the real shape of `GET /api/search/{term}`, captured on
2026-08-07 — including the awkward part, that a search for "Haifa" answers with
Syria, Nablus, the United States and France before it reaches Israel.

Almost everything here goes through a real client session rather than calling
the function, because the schema, the structured content and the error text are
the contract; the Python signature is an implementation detail.
"""

from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import httpx
import pytest
import respx
from mcp.types import TextContent

from ihm_mcp.config import get_settings
from ihm_mcp.errors import InvalidInputError
from ihm_mcp.models import ISRAEL_BBOX, Coordinates
from ihm_mcp.tools.places import normalize_query
from tests.conftest import connected_session

BASE_URL = str(get_settings().base_url).rstrip("/")
SEARCH_PATH = "/api/search/"


def upstream_place(
    id: str, display_name: str, lat: float, lng: float, **overrides: Any
) -> dict[str, Any]:
    """One item in the upstream response, with the fields it really carries."""
    return {
        "id": id,
        "source": "OSM",
        "title": display_name.split(",")[0],
        "displayName": display_name,
        "icon": "icon-search",
        "iconColor": "black",
        "location": {"lat": lat, "lng": lng, "alt": None},
        "hasExtraData": False,
        **overrides,
    }


# Upstream's own order for "Haifa": relevance, with no regard for country.
HAIFA_RESPONSE = [
    upstream_place(
        "node_1656107649",
        "Haifa, Haifa Subdistrict, Israel",
        32.81912207506254,
        34.998385570943356,
        icon="icon-home",
    ),
    upstream_place(
        "node_567059530",
        "Haifa, Al-Malikiya Subdistrict, Syria",
        37.15612917406765,
        42.29116637259722,
    ),
    # Nablus sits inside the bounding box. The box is a ranking device, not a
    # border, and this fixture is here so that stays visible.
    upstream_place("node_432127476", "Haifa, نابلس", 32.22484710149918, 35.22755786776543),
    upstream_place(
        "way_807000965",
        "Haifa, Washington Township, United States",
        39.74102192552339,
        -77.50450297398471,
    ),
    upstream_place(
        "way_858721035", "Haifa, Lunel-Viel, France", 43.68096438809349, 4.097289675779109
    ),
    upstream_place("way_35223231", "Haifa, Yafia, Israel", 32.6791062, 35.2641834),
]

ISRAELI_IDS = ["node_1656107649", "node_432127476", "way_35223231"]


@pytest.fixture
def mock_upstream() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


@pytest.fixture
def search(mock_upstream: respx.MockRouter) -> respx.Route:
    """One route for every term; tests inspect the request to check encoding."""
    return mock_upstream.get(path__startswith=SEARCH_PATH).mock(
        return_value=httpx.Response(200, json=HAIFA_RESPONSE)
    )


async def call(**arguments: Any) -> dict[str, Any]:
    async with connected_session() as session:
        result = await session.call_tool("search_places", arguments)

    assert result.isError is False, result.content
    assert result.structuredContent is not None
    return result.structuredContent


async def call_expecting_error(**arguments: Any) -> str:
    async with connected_session() as session:
        result = await session.call_tool("search_places", arguments)

    assert result.isError is True
    (block,) = result.content
    assert isinstance(block, TextContent)
    return block.text


def ids(result: dict[str, Any]) -> list[str]:
    return [place["ref"]["identifier"] for place in result["places"]]


# --- the query ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Haifa  ", "Haifa"),
        ("Mount\tMeron", "Mount Meron"),
        ("Ein  Hemed", "Ein Hemed"),
    ],
)
def test_the_query_is_trimmed_and_its_whitespace_collapsed(raw: str, expected: str):
    assert normalize_query(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "\n\t", "a", " x "])
def test_a_query_with_nothing_to_search_for_is_rejected(raw: str):
    with pytest.raises(InvalidInputError):
        normalize_query(raw)


async def test_a_blank_query_is_rejected_without_troubling_upstream(search: respx.Route):
    """Whitespace answers HTTP 500 upstream — this server does not ask."""
    text = await call_expecting_error(query="   ")

    assert "[invalid_input]" in text
    assert search.call_count == 0


async def test_the_term_sent_upstream_is_the_trimmed_one(search: respx.Route):
    result = await call(query="  Haifa ")

    assert result["query"] == "Haifa"
    assert search.calls.last.request.url.raw_path.startswith(b"/api/search/Haifa?")


async def test_hebrew_terms_are_percent_encoded(search: respx.Route):
    await call(query="עין חמד")

    raw_path = search.calls.last.request.url.raw_path.decode()
    assert raw_path.startswith(f"{SEARCH_PATH}{quote('עין חמד', safe='')}?")


async def test_a_slash_in_the_query_cannot_reach_a_different_endpoint(
    search: respx.Route,
):
    await call(query="../urls/some-share-id")

    raw_path = search.calls.last.request.url.raw_path.decode()
    assert raw_path.startswith(f"{SEARCH_PATH}..%2Furls%2Fsome-share-id?")


# --- ranking and filtering ---------------------------------------------------


async def test_israeli_matches_come_back_and_the_rest_do_not(search: respx.Route):
    result = await call(query="Haifa")

    assert result["israelOnly"] is True
    assert ids(result) == ISRAELI_IDS
    assert all(place["inIsrael"] for place in result["places"])
    assert "3 match(es) outside Israel were omitted" in " ".join(result["warnings"])


async def test_worldwide_matches_are_ranked_after_the_local_ones(search: respx.Route):
    result = await call(query="Haifa", israelOnly=False)

    assert ids(result) == [
        # Upstream relevance order is preserved inside each group: the sort is
        # stable and keys only on which side of the box a result falls.
        *ISRAELI_IDS,
        "node_567059530",
        "way_807000965",
        "way_858721035",
    ]
    assert [place["inIsrael"] for place in result["places"]] == [True] * 3 + [False] * 3
    assert result["warnings"] == []


async def test_limit_truncates_and_says_how_much_was_left(search: respx.Route):
    result = await call(query="Haifa", limit=2)

    assert ids(result) == ISRAELI_IDS[:2]
    assert "Showing 2 of 3 matches" in " ".join(result["warnings"])


async def test_no_matches_at_all_is_an_empty_list_not_a_failure(
    mock_upstream: respx.MockRouter,
):
    mock_upstream.get(path__startswith=SEARCH_PATH).mock(
        return_value=httpx.Response(200, json=[])
    )

    result = await call(query="zzzqqqxyznothing")

    assert result["places"] == []
    assert "Nothing matched this name" in " ".join(result["warnings"])


async def test_matches_only_outside_israel_say_where_they_are(
    mock_upstream: respx.MockRouter,
):
    """Silently returning nothing would read as "this place does not exist"."""
    mock_upstream.get(path__startswith=SEARCH_PATH).mock(
        return_value=httpx.Response(
            200, json=[place for place in HAIFA_RESPONSE if place["id"] == "way_858721035"]
        )
    )

    result = await call(query="Haifa")

    assert result["places"] == []
    warnings = " ".join(result["warnings"])
    assert "No matches inside Israel, but 1 matched elsewhere" in warnings
    assert "israelOnly" in warnings


# --- the shape of a result ---------------------------------------------------


async def test_a_result_carries_its_identity_link_and_provenance(search: respx.Route):
    result = await call(query="Haifa")
    haifa = result["places"][0]

    assert haifa["ref"] == {"source": "OSM", "identifier": "node_1656107649"}
    assert haifa["title"] == "Haifa"
    assert haifa["displayName"] == "Haifa, Haifa Subdistrict, Israel"
    assert haifa["coordinates"] == {"lat": 32.81912207506254, "lng": 34.998385570943356}
    assert haifa["ihmUrl"] == f"{BASE_URL}/poi/OSM/node_1656107649?language=en"
    assert "CC BY-NC-SA" in " ".join(result["attribution"]["sources"])
    assert result["attribution"]["notice"]


async def test_language_reaches_upstream_and_the_link(search: respx.Route):
    result = await call(query="חיפה", language="he")

    assert search.calls.last.request.url.params["language"] == "he"
    assert result["language"] == "he"
    assert result["places"][0]["ihmUrl"].endswith("?language=he")


async def test_a_nameless_feature_is_still_labelled(mock_upstream: respx.MockRouter):
    mock_upstream.get(path__startswith=SEARCH_PATH).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "way_1",
                    "source": "OSM",
                    "location": {"lat": 32.0, "lng": 35.0},
                }
            ],
        )
    )

    result = await call(query="unnamed")

    assert result["places"][0]["title"] == "way_1"
    assert result["places"][0]["displayName"] == "way_1"


def test_the_bounding_box_edges_are_inclusive():
    assert ISRAEL_BBOX.contains(Coordinates(lat=29.3, lng=34.2))
    assert ISRAEL_BBOX.contains(Coordinates(lat=33.4, lng=35.9))
    assert not ISRAEL_BBOX.contains(Coordinates(lat=33.5, lng=35.0))
    assert not ISRAEL_BBOX.contains(Coordinates(lat=32.0, lng=36.0))


# --- when upstream stops making sense ----------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"results": []}, id="object instead of array"),
        pytest.param([{"id": "way_1", "source": "OSM"}], id="no location"),
        pytest.param(
            [{"id": "way_1", "source": "OSM", "location": {"lat": 32.0}}], id="no lng"
        ),
        pytest.param(
            [{"id": "way_1", "source": "OSM", "location": {"lat": 932.0, "lng": 35.0}}],
            id="impossible latitude",
        ),
        pytest.param(["Haifa"], id="array of strings"),
    ],
)
async def test_an_unrecognised_response_is_reported_not_guessed_at(
    mock_upstream: respx.MockRouter, payload: Any
):
    mock_upstream.get(path__startswith=SEARCH_PATH).mock(
        return_value=httpx.Response(200, json=payload)
    )

    text = await call_expecting_error(query="Haifa")

    assert "[upstream_schema_changed]" in text


async def test_a_404_means_the_endpoint_moved_not_that_the_place_is_unknown(
    mock_upstream: respx.MockRouter,
):
    """A term nobody matches answers 200 with `[]`, so a 404 is about the API."""
    mock_upstream.get(path__startswith=SEARCH_PATH).mock(
        return_value=httpx.Response(404)
    )

    text = await call_expecting_error(query="Haifa")

    assert "[upstream_schema_changed]" in text
    assert "place_not_found" not in text


async def test_upstream_failures_keep_their_taxonomy_code(
    mock_upstream: respx.MockRouter,
):
    mock_upstream.get(path__startswith=SEARCH_PATH).mock(
        side_effect=httpx.ReadTimeout
    )

    text = await call_expecting_error(query="Haifa")

    assert "[upstream_timeout]" in text
