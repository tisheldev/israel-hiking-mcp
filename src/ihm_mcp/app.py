"""The FastMCP application object and everything a tool call can reach.

This lives apart from `server.py` so imports run one way: tool modules import
the app, and the entry point imports both. Keeping the `FastMCP` instance in
the entry point would force every `tools/*` module to import the module that
imports it.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, TypeVar

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from mcp.types import ToolAnnotations

from ihm_mcp import SERVER_NAME, __version__
from ihm_mcp.config import Settings, get_settings
from ihm_mcp.ihm_client import UpstreamClient

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class AppContext:
    """What a tool call needs from the process, reachable via `Context`."""

    settings: Settings
    ihm: UpstreamClient


#: What a tool annotates its context parameter with, e.g. `ctx: ToolContext`.
ToolContext = Context[ServerSession, AppContext]


def app_context(ctx: ToolContext) -> AppContext:
    """The lifespan context, named so tools do not repeat the attribute walk."""
    return ctx.request_context.lifespan_context


@asynccontextmanager
async def lifespan(_: FastMCP) -> AsyncIterator[AppContext]:
    """Own the HTTP client for the life of the session.

    One connection pool and one cache for the whole process; building them per
    tool call would throw both away.
    """
    settings = get_settings()
    async with UpstreamClient(settings) as ihm:
        logger.info("upstream client ready for %s", settings.base_url)
        yield AppContext(settings=settings, ihm=ihm)


mcp = FastMCP(SERVER_NAME, lifespan=lifespan)

# FastMCP has no `version` argument, and the low-level server falls back to the
# MCP SDK's own version in the initialize response. Report ours instead.
mcp._mcp_server.version = __version__


def tool(**kwargs: Any) -> Callable[[F], F]:
    """Register an MCP tool, using the docstring as its description.

    FastMCP passes `__doc__` through verbatim, which leaves Python's source
    indentation in the text the model reads in `tools/list`.
    """

    def register(fn: F) -> F:
        kwargs.setdefault("description", inspect.cleandoc(fn.__doc__ or ""))
        # This server only ever reads; saying so lets a host skip a confirmation.
        kwargs.setdefault("annotations", ToolAnnotations(readOnlyHint=True))
        return mcp.tool(**kwargs)(fn)

    return register
