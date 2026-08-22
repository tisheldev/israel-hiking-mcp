"""Protocol-level contract for the server: handshake, tools/list, tools/call.

Every tool this server exposes talks to somebody else's host, so the one call
made here is mocked at the transport. What is being checked is not the answer —
the tool suites do that — but the envelope around it: that a result arrives
structured, that it is mirrored as text for hosts which ignore the structured
channel, and that bad arguments never reach a tool body at all.
"""

import json
from collections.abc import Iterator

import httpx
import pytest
import respx
from mcp.types import TextContent

from ihm_mcp.config import get_settings
from tests.conftest import connected_session

BASE_URL = str(get_settings().base_url).rstrip("/")

#: One place, in the shape `/api/search/{term}` really answers with.
HAIFA = [
    {
        "id": "node_29429711",
        "source": "OSM",
        "title": "Haifa",
        "displayName": "Haifa, Haifa Subdistrict, Israel",
        "location": {"lat": 32.8191218, "lng": 34.9983856},
        "hasExtraData": True,
    }
]

TOOLS = [
    "find_pois_along_route",
    "get_route_details",
    "route_between_points",
    "search_hiking_routes",
    "search_places",
]


@pytest.fixture
def one_place() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(path__startswith="/api/search/").mock(
            return_value=httpx.Response(200, json=HAIFA)
        )
        yield router


async def test_tools_list_exposes_every_registered_tool():
    async with connected_session() as session:
        tools = (await session.list_tools()).tools

    assert sorted(t.name for t in tools) == TOOLS

    for tool in tools:
        # The LLM picks tools by description; never leave one empty, and never
        # leak Python's source indentation into the text it reads.
        assert tool.description
        assert not any(line.startswith(" ") for line in tool.description.splitlines())
        assert tool.inputSchema["type"] == "object"
        # Nothing here writes anywhere, and saying so can save a confirmation.
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True


async def test_a_schema_marks_only_the_arguments_without_defaults_required():
    async with connected_session() as session:
        tools = (await session.list_tools()).tools
        (routing,) = [t for t in tools if t.name == "route_between_points"]

    assert sorted(routing.inputSchema["required"]) == ["end", "start"]
    # `activity` has a default, so a caller may leave it out.
    assert routing.inputSchema["properties"]["activity"]["default"] == "Hiking"


async def test_server_advertises_identity_and_tool_capability():
    async with connected_session() as session:
        capabilities = session.get_server_capabilities()

    assert capabilities is not None
    assert capabilities.tools is not None


async def test_a_successful_call_returns_structured_content(one_place: respx.MockRouter):
    async with connected_session() as session:
        result = await session.call_tool("search_places", {"query": "Haifa"})

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["query"] == "Haifa"
    assert [place["title"] for place in result.structuredContent["places"]] == ["Haifa"]

    # Structured results are mirrored as text for hosts that ignore the
    # structured channel.
    (block,) = result.content
    assert isinstance(block, TextContent)
    assert json.loads(block.text)["places"][0]["ref"]["source"] == "OSM"


async def test_an_argument_left_out_falls_back_to_its_default(
    one_place: respx.MockRouter,
):
    async with connected_session() as session:
        result = await session.call_tool("search_places", {"query": "Haifa"})

    assert result.structuredContent["language"] == "en"
    assert result.structuredContent["israelOnly"] is True


async def test_wrong_argument_type_is_rejected_at_the_schema_boundary():
    """Validation happens before the tool body runs — every tool relies on it."""
    async with connected_session() as session:
        result = await session.call_tool("search_places", {"query": 123})

    assert result.isError is True
    (block,) = result.content
    assert isinstance(block, TextContent)
    assert "query" in block.text


async def test_unknown_tool_is_an_error_not_a_crash():
    async with connected_session() as session:
        result = await session.call_tool("does_not_exist", {})

    assert result.isError is True
    (block,) = result.content
    assert isinstance(block, TextContent)
    assert "does_not_exist" in block.text
