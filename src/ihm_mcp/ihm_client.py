"""The single door to the outside world.

Everything this server fetches goes through `UpstreamClient`, so the courtesy
rules — timeouts, a connection cap, an honest User-Agent, caching, one retry —
live here instead of being re-implemented per tool.

It knows HTTP, not hiking: a 404 becomes `UpstreamNotFound` and the calling
tool decides what that means.
"""

from __future__ import annotations

import logging
import random
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any, Self, cast

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

MISS = object()

Params = Mapping[str, Any] | None


class Cache:
    """TTL + LRU, bounded because one route search fans out over a hundred tiles."""

    def __init__(self, *, ttl: float, max_entries: int, clock: Callable[[], float]) -> None:
        self.ttl = ttl
        self.max_entries = max_entries
        self.clock = clock
        self.entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any:
        entry = self.entries.get(key)
        if entry is None:
            return MISS
        expires_at, value = entry
        if self.clock() >= expires_at:
            del self.entries[key]
            return MISS
        self.entries.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        if self.ttl <= 0:
            return
        self.entries[key] = (self.clock() + self.ttl, value)
        self.entries.move_to_end(key)
        while len(self.entries) > self.max_entries:
            self.entries.popitem(last=False)


class UpstreamClient:
    """A shared, capped, cached `httpx.AsyncClient` for one upstream host."""

    def __init__(
        self,
        settings: Settings,
        *,
        base_url: str | None = None,
        retry_backoff_seconds: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.retry_backoff_seconds = retry_backoff_seconds
        self.limiter = anyio.Semaphore(settings.max_concurrent_requests)
        self.cache = Cache(
            ttl=settings.cache_ttl_seconds,
            max_entries=settings.cache_max_entries,
            clock=clock,
        )
        timeout = settings.request_timeout_seconds
        self.http = httpx.AsyncClient(
            base_url=base_url or str(settings.base_url),
            # Every phase, not just the read: the failure that actually bites is
            # a host that accepts the connection and then goes quiet.
            timeout=httpx.Timeout(
                connect=min(5.0, timeout), read=timeout, write=timeout, pool=timeout
            ),
            limits=httpx.Limits(max_connections=settings.max_concurrent_requests),
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.http.aclose()

    async def get_json(self, path: str, *, params: Params = None) -> Any:
        key = f"json:{self.url(path, params)}"
        cached = self.cache.get(key)
        if cached is MISS:
            response = await self.get(path, params, accept="application/json")
            cached = self.parse_json(response)
            self.cache.set(key, cached)
        return cached

    async def get_bytes(
        self, path: str, *, params: Params = None, accept: str | None = None
    ) -> bytes:
        key = f"bytes:{self.url(path, params)}"
        cached = self.cache.get(key)
        if cached is MISS:
            response = await self.get(path, params, accept=accept)
            cached = response.content
            self.cache.set(key, cached)
        return cast(bytes, cached)

    async def get(self, path: str, params: Params, *, accept: str | None) -> httpx.Response:
        """Send the request, retrying once — and only once — if it fails.

        A second failure means the host is genuinely unhappy, and hammering it
        is what this server must avoid. Every request is a GET, which is what
        makes retrying safe at all; adding a POST means revisiting this.
        """
        try:
            return await self.send(path, params, accept)
        except (UpstreamTimeoutError, UpstreamUnavailableError) as failure:
            logger.info("retrying %s after: %s", path, failure)
            # Jitter so concurrent tile fetches do not all retry in step.
            await anyio.sleep(self.retry_backoff_seconds * random.uniform(1.0, 1.5))
            return await self.send(path, params, accept)

    async def send(self, path: str, params: Params, accept: str | None) -> httpx.Response:
        """One request, with every way it can fail mapped to a typed error."""
        try:
            async with self.limiter:
                response = await self.http.get(
                    path,
                    params=sorted_params(params),
                    headers={"Accept": accept} if accept else None,
                )
        except httpx.TimeoutException:
            raise UpstreamTimeoutError(
                f"{self.host} did not respond within "
                f"{self.settings.request_timeout_seconds:g}s."
            ) from None
        except httpx.TransportError as exc:
            # The exception's own text carries the full URL, so keep only its class.
            raise UpstreamUnavailableError(
                f"Could not reach {self.host} ({type(exc).__name__})."
            ) from None

        status = response.status_code
        if status >= 500:
            raise UpstreamUnavailableError(f"{self.host} returned HTTP {status} for {path}.")
        # 410 alongside 404: OpenStreetMap answers Gone for an element somebody
        # deleted, which to a caller asking for it is the same missing thing.
        if status in (404, 410):
            raise UpstreamNotFound(path)
        if status == 429:
            retry_after = response.headers.get("retry-after", "").strip()
            wait = f" Retry after {retry_after}s." if retry_after.isdigit() else ""
            raise RateLimitedError(f"{self.host} is rate limiting this client.{wait}")
        if status >= 400:
            # A request we believed was valid was refused: a contract mismatch
            # rather than an outage, so not worth a retry.
            raise UpstreamSchemaChangedError(
                f"{self.host} rejected the request for {path} with HTTP {status}."
            )
        return response

    def parse_json(self, response: httpx.Response) -> Any:
        content_type = response.headers.get("content-type", "")
        if content_type and "json" not in content_type:
            raise UpstreamSchemaChangedError(
                f"{self.host} returned {content_type} where JSON was expected."
            )
        try:
            return response.json()
        except ValueError:
            # Never quote the body: it may be a full HTML error page, and this
            # string goes straight into a model's context.
            raise UpstreamSchemaChangedError(
                f"{self.host} returned a body that is not valid JSON."
            ) from None

    @property
    def host(self) -> str:
        return self.http.base_url.host

    def url(self, path: str, params: Params) -> httpx.URL:
        return self.http.build_request("GET", path, params=sorted_params(params)).url


def sorted_params(params: Params) -> list[tuple[str, Any]] | None:
    """Sorted so the same request written two ways shares one cache entry."""
    return sorted(params.items()) if params else None
