from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from ihm_mcp.server import mcp


@asynccontextmanager
async def connected_session() -> AsyncIterator[ClientSession]:
    """A client already through the initialize handshake, over in-memory streams.

    Deliberately a context manager rather than a fixture: the SDK's helper holds
    anyio cancel scopes, and an async-generator fixture would enter and exit them
    from different tasks.
    """
    async with create_connected_server_and_client_session(mcp) as client:
        yield client
