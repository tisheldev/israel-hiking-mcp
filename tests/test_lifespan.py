"""The HTTP client's lifetime is tied to the session, not to a tool call."""

from ihm_mcp.app import lifespan, mcp
from ihm_mcp.config import get_settings
from ihm_mcp.ihm_client import UpstreamClient
from tests.conftest import connected_session


async def test_lifespan_shares_one_client_per_upstream_and_closes_them_on_shutdown():
    """Two hosts with two usage policies get two pools and two caches."""
    async with lifespan(mcp) as context:
        assert isinstance(context.ihm, UpstreamClient)
        assert isinstance(context.osm, UpstreamClient)
        assert context.settings is get_settings()
        assert context.ihm.host == "mapeak.com"
        assert context.osm.host == "api.openstreetmap.org"
        clients = (context.ihm, context.osm)
        assert all(client.http.is_closed is False for client in clients)

    # A leaked connection pool would outlive the server it belongs to.
    assert all(client.http.is_closed is True for client in clients)


async def test_tools_still_work_with_the_lifespan_wired_in():
    """The in-memory helper runs the real startup path — this is the smoke test."""
    async with connected_session() as session:
        result = await session.call_tool("ping", {})

    assert result.isError is False
