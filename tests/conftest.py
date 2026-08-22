from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from ihm_mcp.server import mcp


@asynccontextmanager
async def connected_session(server: FastMCP | None = None) -> AsyncIterator[ClientSession]:
    """A client already through the initialize handshake, over in-memory streams.

    Defaults to the real server; tests that need a tool the real server does not
    expose (an error-boundary probe, say) pass their own throwaway `FastMCP`.

    Deliberately a context manager rather than a fixture: the SDK's helper holds
    anyio cancel scopes, and an async-generator fixture would enter and exit them
    from different tasks.
    """
    async with create_connected_server_and_client_session(server or mcp) as client:
        yield client


def tags(mapping: dict[str, str]) -> dict[str, Any]:
    """Localized property names like `name:he` are not Python identifiers, so
    they reach a fixture helper as a mapping rather than as keywords. Widening
    the value type here is what lets one be unpacked beside keyword arguments
    that are not strings."""
    return dict(mapping)
