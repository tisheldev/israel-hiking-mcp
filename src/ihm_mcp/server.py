"""MCP server entry point.

Transport note: on stdio, stdout *is* the JSON-RPC channel. Anything written
there that is not a protocol message corrupts the stream and breaks the client,
so this process never prints to stdout — logging is pinned to stderr.
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from ihm_mcp import ATTRIBUTION, SERVER_NAME, __version__

logger = logging.getLogger("ihm_mcp")

F = TypeVar("F", bound=Callable[..., Any])


def configure_logging(level: int | str | None = None) -> None:
    """Send all log records to stderr, replacing any inherited handlers."""
    resolved = level if level is not None else os.getenv("IHM_LOG_LEVEL", "INFO")
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(resolved)


mcp = FastMCP(SERVER_NAME)

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
        return mcp.tool(**kwargs)(fn)

    return register


class PingResult(BaseModel):
    """Liveness response carrying the server identity and its attribution."""

    status: str = Field(description="Always 'ok' when the server is reachable.")
    server: str = Field(description="MCP server name.")
    version: str = Field(description="Server version.")
    attribution: str = Field(description="Required data attribution notice.")
    echo: str = Field(description="The caller's echo string, returned verbatim.")


@tool()
def ping(echo: str = "pong") -> PingResult:
    """Check that the server is alive and report its version and data attribution.

    This tool touches no upstream service. It is a placeholder used to verify
    transport and tool wiring, and will be removed once real tools exist.
    """
    logger.debug("ping called with echo=%r", echo)
    return PingResult(
        status="ok",
        server=SERVER_NAME,
        version=__version__,
        attribution=ATTRIBUTION,
        echo=echo,
    )


def main() -> None:
    configure_logging()
    logger.info("starting %s v%s on stdio", SERVER_NAME, __version__)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
