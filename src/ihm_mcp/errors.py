"""The error taxonomy every tool speaks.

MCP has no structured error channel: FastMCP turns a raised exception into
"Error executing tool <name>: <str(exc)>" and drops everything else, so
`str(exc)` is the entire message the model gets. That is why the code leads the
text, and why messages are sanitized — httpx exceptions stringify to full
request URLs, and a failing upstream may answer with an HTML page.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, TypeVar

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 240
RETRY_HINT = "This is usually transient; the same call may succeed later."

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def one_line(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > MAX_MESSAGE_CHARS:
        return collapsed[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"
    return collapsed


class IhmError(Exception):
    """Base class for every failure a tool is allowed to report."""

    code: ClassVar[str]
    retryable: ClassVar[bool] = False

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        self.message = one_line(message)
        self.hint = one_line(hint) if hint else RETRY_HINT if self.retryable else None
        super().__init__(self.message)

    def __str__(self) -> str:
        text = f"[{self.code}] {self.message}"
        return f"{text} {self.hint}" if self.hint else text


class InvalidInputError(IhmError):
    code = "invalid_input"


class PlaceNotFoundError(IhmError):
    code = "place_not_found"


class RouteNotFoundError(IhmError):
    code = "route_not_found"


class UnsupportedSourceError(IhmError):
    """This server cannot resolve that source — better than guessing a URL."""

    code = "unsupported_source"


class SearchAreaTooLargeError(IhmError):
    code = "search_area_too_large"


class GeometryTooLargeError(IhmError):
    code = "geometry_too_large"


class UpstreamTimeoutError(IhmError):
    code = "upstream_timeout"
    retryable = True


class UpstreamUnavailableError(IhmError):
    code = "upstream_unavailable"
    retryable = True


class UpstreamSchemaChangedError(IhmError):
    """Upstream answered in a shape we do not understand.

    The APIs behind this server have no published contract, so this is the
    fail-safe: report the mismatch rather than emit confidently wrong data.
    """

    code = "upstream_schema_changed"


class RateLimitedError(IhmError):
    code = "rate_limited"
    retryable = True


class UpstreamNotFound(Exception):
    """Internal: a 404. Outside the taxonomy so a leak cannot pass for an answer.

    The HTTP client cannot know whether a missing resource means
    `place_not_found` or `route_not_found`, so the calling tool translates it.
    """


def tool_errors(fn: F) -> F:
    """Let only taxonomy errors cross the MCP boundary; log the rest."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except IhmError:
            raise
        except Exception as exc:
            logger.exception("unexpected error in tool %s", fn.__name__)
            raise UpstreamUnavailableError(f"{fn.__name__} failed unexpectedly.") from exc

    return wrapper  # type: ignore[return-value]
