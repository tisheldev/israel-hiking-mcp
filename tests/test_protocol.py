"""Protocol-level contract for the server: handshake, tools/list, tools/call."""

import json

from mcp.types import TextContent

from ihm_mcp import ATTRIBUTION, SERVER_NAME, __version__
from tests.conftest import connected_session


async def test_tools_list_exposes_ping_with_a_schema():
    async with connected_session() as session:
        tools = (await session.list_tools()).tools

    assert [t.name for t in tools] == ["ping"]

    ping = tools[0]
    assert ping.description  # the LLM picks tools by description; never leave it empty
    # Descriptions are what the model reads, so no leaked source indentation.
    assert not any(line.startswith(" ") for line in ping.description.splitlines())
    assert ping.inputSchema["type"] == "object"
    assert ping.inputSchema["properties"]["echo"]["type"] == "string"
    # `echo` has a default, so nothing is required.
    assert not ping.inputSchema.get("required")


async def test_server_advertises_identity_and_tool_capability():
    async with connected_session() as session:
        capabilities = session.get_server_capabilities()

    assert capabilities is not None
    assert capabilities.tools is not None


async def test_calling_ping_returns_structured_content():
    async with connected_session() as session:
        result = await session.call_tool("ping", {"echo": "hello"})

    assert result.isError is False
    assert result.structuredContent == {
        "status": "ok",
        "server": SERVER_NAME,
        "version": __version__,
        "attribution": ATTRIBUTION,
        "echo": "hello",
    }

    # Structured results are mirrored as text for hosts that ignore the
    # structured channel.
    (block,) = result.content
    assert isinstance(block, TextContent)
    assert json.loads(block.text)["version"] == __version__


async def test_ping_echo_defaults():
    async with connected_session() as session:
        result = await session.call_tool("ping", {})

    assert result.structuredContent["echo"] == "pong"


async def test_wrong_argument_type_is_rejected_at_the_schema_boundary():
    """Validation happens before the tool body runs — later PRs rely on this."""
    async with connected_session() as session:
        result = await session.call_tool("ping", {"echo": 123})

    assert result.isError is True
    (block,) = result.content
    assert isinstance(block, TextContent)
    assert "echo" in block.text


async def test_unknown_tool_is_an_error_not_a_crash():
    async with connected_session() as session:
        result = await session.call_tool("does_not_exist", {})

    assert result.isError is True
    (block,) = result.content
    assert isinstance(block, TextContent)
    assert "does_not_exist" in block.text
