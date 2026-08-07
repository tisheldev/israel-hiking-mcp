"""The single door to the outside world.

Everything this server fetches goes through `UpstreamClient`, so the courtesy
rules live in one place instead of being re-implemented (and forgotten) per
tool: a timeout on every request, a hard cap on simultaneous connections, an
honest User-Agent, a short-lived cache, and exactly one retry for failures that
are plausibly transient.

The client is deliberately *not* domain-aware. It knows HTTP, not hiking: a 404
becomes `UpstreamNotFound`, and the calling tool decides whether that means
`place_not_found` or `route_not_found`. Pushing that decision down here would
mean teaching the transport about every endpoint.

Every request is a GET, which is what makes retrying safe at all. If a POST is
ever added, the retry logic has to be revisited first.
"""

from __future__ import annotations

import logging
import random
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, Self

import anyio
import httpx

from ihm_mcp.config import Settings
from ihm_mcp.errors import (
    RateLimitedError,
    UpstreamNotFound,
    UpstreamSchemaChangedError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)

logger = logging.getLogger(__name__)

#: One retry, not a retry loop. A second failure means the host is genuinely
#: unhappy, and hammering it is exactly the behaviour this server must avoid.
MAX_ATTEMPTS = 2


class _TtlCache:
    """Tiny TTL + LRU cache.

    Bounded on purpose: a stdio server lives as long as its host, and a single
    route search fans out over a hundred tiles, so an unbounded dict is a slow
    memory leak. Failures are never stored — a cached 500 would poison every
    later call in the session.
    """

    def __init__(self, *, ttl: float, max_entries: int, clock: Callable[[], float]) -> None:
        self._ttl = ttl
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    def get(self, key: str) -> tuple[bool, Any]:
        """Return `(hit, value)` — `None` is a legal cached value, so no sentinel."""
        if not self.enabled:
            return False, None
        entry = self._entries.get(key)
        if entry is None:
            return False, None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return False, None
        self._entries.move_to_end(key)
        return True, value

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        self._entries[key] = (self._clock() + self._ttl, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)


class UpstreamClient:
    """A shared, capped, cached `httpx.AsyncClient` for one upstream host.

    One instance per host: PR 7 adds a second for api.openstreetmap.org, which
    is why the base URL is a constructor argument rather than read from
    settings here.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        base_url: str | None = None,
        retry_backoff_seconds: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._base_url = base_url or str(settings.base_url)
        self._retry_backoff_seconds = retry_backoff_seconds
        self._limiter = anyio.Semaphore(settings.max_concurrent_requests)
        self._cache = _TtlCache(
            ttl=settings.cache_ttl_seconds,
            max_entries=settings.cache_max_entries,
            clock=clock,
        )
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout(settings.request_timeout_seconds),
            limits=httpx.Limits(max_connections=settings.max_concurrent_requests),
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )

    @staticmethod
    def _timeout(seconds: float) -> httpx.Timeout:
        """Bound every phase, not just the read.

        A bare float would leave connect/write/pool at their defaults, and the
        failure mode that actually bites — a host that accepts the connection
        and then goes quiet — hides in the phases people forget to set.
        """
        return httpx.Timeout(
            connect=min(5.0, seconds),
            read=seconds,
            write=seconds,
            pool=seconds,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def is_closed(self) -> bool:
        return self._client.is_closed

    # --- public API ----------------------------------------------------------

    async def get_json(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        """GET `path` and parse the response as JSON."""
        key = self._cache_key("json", path, params)
        hit, value = self._cache.get(key)
        if hit:
            logger.debug("cache hit %s", key)
            return value

        response = await self._get(path, params=params, accept="application/json")
        value = self._parse_json(response)
        self._cache.set(key, value)
        return value

    async def get_bytes(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        accept: str | None = None,
        expected_content_type: str | None = None,
    ) -> bytes:
        """GET `path` and return the raw body (vector tiles, thumbnails)."""
        key = self._cache_key("bytes", path, params)
        hit, value = self._cache.get(key)
        if hit:
            logger.debug("cache hit %s", key)
            return value

        response = await self._get(path, params=params, accept=accept)
        if expected_content_type and not self._content_type(response).startswith(
            expected_content_type
        ):
            raise UpstreamSchemaChangedError(
                f"{self._host} returned {self._content_type(response) or 'no content type'} "
                f"for {path}; expected {expected_content_type}."
            )
        body = response.content
        self._cache.set(key, body)
        return body

    # --- internals -----------------------------------------------------------

    @property
    def _host(self) -> str:
        return self._client.base_url.host

    def _cache_key(self, kind: str, path: str, params: Mapping[str, Any] | None) -> str:
        """Normalize the URL so equivalent requests share a cache entry."""
        request = self._client.build_request("GET", path, params=self._sorted(params))
        return f"{kind}:{request.url}"

    @staticmethod
    def _sorted(params: Mapping[str, Any] | None) -> list[tuple[str, Any]] | None:
        return sorted(params.items()) if params else None

    @staticmethod
    def _content_type(response: httpx.Response) -> str:
        return response.headers.get("content-type", "").split(";")[0].strip().lower()

    async def _get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None,
        accept: str | None,
    ) -> httpx.Response:
        """Send the request, retrying once on a transient failure."""
        headers = {"Accept": accept} if accept else None
        failure: Exception | None = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                async with self._limiter:
                    response = await self._client.get(
                        path, params=self._sorted(params), headers=headers
                    )
            except httpx.TimeoutException:
                failure = UpstreamTimeoutError(
                    f"{self._host} did not respond within "
                    f"{self._settings.request_timeout_seconds:g}s."
                )
            except httpx.TransportError as exc:
                # Connection refused, DNS failure, TLS problem: the exception's
                # own text carries the full URL, so only its class name is kept.
                failure = UpstreamUnavailableError(
                    f"Could not reach {self._host} ({type(exc).__name__})."
                )
            else:
                if response.status_code < 500:
                    self._raise_for_client_error(response, path)
                    return response
                failure = UpstreamUnavailableError(
                    f"{self._host} returned HTTP {response.status_code} for {path}."
                )

            if attempt + 1 < MAX_ATTEMPTS:
                logger.info("retrying %s after: %s", path, failure)
                await self._backoff()

        assert failure is not None  # every path through the loop sets it
        raise failure

    async def _backoff(self) -> None:
        if self._retry_backoff_seconds <= 0:
            return
        # A little jitter so concurrent tile fetches do not all retry in step.
        await anyio.sleep(self._retry_backoff_seconds * random.uniform(1.0, 1.5))

    def _raise_for_client_error(self, response: httpx.Response, path: str) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 404:
            raise UpstreamNotFound(path)
        if status == 429:
            raise RateLimitedError(
                f"{self._host} is rate limiting this client.{self._retry_after(response)}"
            )
        # Any other 4xx means a request we believed was valid was refused, which
        # is a contract mismatch rather than an outage — worth surfacing loudly
        # instead of inviting a pointless retry.
        raise UpstreamSchemaChangedError(
            f"{self._host} rejected the request for {path} with HTTP {status}."
        )

    @staticmethod
    def _retry_after(response: httpx.Response) -> str:
        value = response.headers.get("retry-after", "").strip()
        return f" Retry after {value}s." if value.isdigit() else ""

    def _parse_json(self, response: httpx.Response) -> Any:
        content_type = self._content_type(response)
        if content_type and "json" not in content_type:
            raise UpstreamSchemaChangedError(
                f"{self._host} returned {content_type} where JSON was expected."
            )
        try:
            return response.json()
        except ValueError:
            # The body is never quoted: it may be a full HTML error page, and
            # this string goes straight into a model's context.
            raise UpstreamSchemaChangedError(
                f"{self._host} returned a body that is not valid JSON."
            ) from None
