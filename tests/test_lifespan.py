"""The HTTP client's lifetime is tied to the session, not to a tool call."""

from ihm_mcp.config import get_settings
from ihm_mcp.ihm_client import UpstreamClient
from ihm_mcp.server import lifespan, mcp
from tests.conftest import connected_session


async def test_lifespan_shares_one_client_and_closes_it_on_shutdown():
    async with lifespan(mcp) as context:
        assert isinstance(context.ihm, UpstreamClient)
        assert context.settings is get_settings()
        assert context.ihm.http.is_closed is False
        client = context.ihm

    # A leaked connection pool would outlive the server it belongs to.
    assert client.http.is_closed is True


async def test_tools_still_work_with_the_lifespan_wired_in():
    """The in-memory helper runs the real startup path — this is the smoke test."""
    async with connected_session() as session:
        result = await session.call_tool("ping", {})

    assert result.isError is False
