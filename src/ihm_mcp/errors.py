"""The error taxonomy every tool speaks.

MCP has no structured error channel: when a tool raises, FastMCP wraps the
exception as `Error executing tool <name>: <str(exc)>` and the low-level server
turns that single string into the whole `isError` result. `structuredContent`
is dropped. So `str(exc)` is the entire message the model gets, which forces
two rules:

1. **The code lives in the text.** `[upstream_timeout] ...` — a model (or a
   human reading a transcript) can tell "try again" from "this will never work"
   without parsing anything.
2. **Nothing raw escapes.** An `httpx` exception stringifies to the full
   request URL; a failing upstream may answer with an HTML error page. Neither
   belongs in a model's context, so every exception leaving a tool passes
   through `tool_errors`, and messages are sanitized to one short line.

Numbers that matter to the caller (a tile count, a limit) go *in the message*,
because there is nowhere else for them to go.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any, ClassVar, TypeVar

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 240

F = TypeVar("F", bound=Callable[..., Any])

_RETRY_HINT = "This is usually transient; the same call may succeed later."


def _sanitize(text: str) -> str:
    """One short line: no newlines, no runaway upstream bodies."""
    collapsed = " ".join(text.split())
    if len(collapsed) > MAX_MESSAGE_CHARS:
        collapsed = collapsed[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"
    return collapsed


class IhmError(Exception):
    """Base class for every failure a tool is allowed to report.

    Subclasses set `code`, which is what the caller keys off. `retryable` only
    changes the advice appended to the message — no retrying happens here.
    """

    code: ClassVar[str] = ""
    retryable: ClassVar[bool] = False

    registry: ClassVar[dict[str, type[IhmError]]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.code:  # pragma: no cover - guards against a silent mistake
            raise TypeError(f"{cls.__name__} must define a non-empty code")
        existing = IhmError.registry.get(cls.code)
        if existing is not None:  # pragma: no cover - same
            raise TypeError(f"code {cls.code!r} already used by {existing.__name__}")
        IhmError.registry[cls.code] = cls

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        self.message = _sanitize(message)
        self.hint = _sanitize(hint) if hint else (_RETRY_HINT if self.retryable else None)
        super().__init__(self.message)

    def __str__(self) -> str:
        text = f"[{self.code}] {self.message}"
        return f"{text} {self.hint}" if self.hint else text


class InvalidInputError(IhmError):
    """Arguments were well-typed but unusable — out of range, empty, malformed."""

    code = "invalid_input"


class PlaceNotFoundError(IhmError):
    """No place matches the query."""

    code = "place_not_found"


class RouteNotFoundError(IhmError):
    """The referenced route does not exist, or is no longer published."""

    code = "route_not_found"


class UnsupportedSourceError(IhmError):
    """The reference names a source this server cannot resolve.

    Raised instead of guessing a URL: a wrong guess would be indistinguishable
    from a real answer.
    """

    code = "unsupported_source"


class SearchAreaTooLargeError(IhmError):
    """The requested area needs more upstream tiles than the budget allows."""

    code = "search_area_too_large"


class GeometryTooLargeError(IhmError):
    """A geometry is too large to return even after simplification."""

    code = "geometry_too_large"


class UpstreamTimeoutError(IhmError):
    """Upstream did not answer in time."""

    code = "upstream_timeout"
    retryable = True


class UpstreamUnavailableError(IhmError):
    """Upstream refused, failed, or could not be reached."""

    code = "upstream_unavailable"
    retryable = True


class UpstreamSchemaChangedError(IhmError):
    """Upstream answered, but not in a shape this server understands.

    The APIs behind this server have no published contract, so this is the
    fail-safe: report the mismatch rather than emit confidently wrong data.
    """

    code = "upstream_schema_changed"


class RateLimitedError(IhmError):
    """Upstream asked us to slow down."""

    code = "rate_limited"
    retryable = True


class UpstreamNotFound(Exception):
    """Internal: a 404 from an upstream request. Never reaches a client.

    The HTTP client cannot know what a missing resource *means* — a 404 from
    `/api/search` is `place_not_found`, from `/api/urls/{id}` it is
    `route_not_found` — so it raises this and each tool translates it. Keeping
    it outside the `IhmError` hierarchy makes an untranslated leak impossible
    to mistake for a real answer: `tool_errors` turns it into a generic
    upstream failure.
    """


def tool_errors(fn: F) -> F:
    """Guard a tool body so only taxonomy errors cross the MCP boundary.

    `IhmError` passes through untouched. Anything else is a bug or an
    unanticipated upstream behaviour: the caller gets a generic
    `upstream_unavailable`, and the real exception — with traceback — goes to
    the stderr log where it is useful and harmless.
    """

    def handle(exc: BaseException, name: str) -> IhmError:
        logger.exception("unexpected error in tool %s", name, exc_info=exc)
        return UpstreamUnavailableError(f"{name} failed unexpectedly.")

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except IhmError:
                raise
            except Exception as exc:
                raise handle(exc, fn.__name__) from exc

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except IhmError:
            raise
        except Exception as exc:
            raise handle(exc, fn.__name__) from exc

    return wrapper  # type: ignore[return-value]
