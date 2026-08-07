"""What a failing tool actually looks like on the wire.

The unit tests in `test_errors.py` check `str(exc)`. This one checks the thing
that matters: that the string survives FastMCP's wrapping and reaches the client
as an `isError` result with the code still readable — because that text is all
a model gets to reason about.
"""

import logging

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from ihm_mcp.errors import (
    InvalidInputError,
    UpstreamNotFound,
    UpstreamTimeoutError,
    tool_errors,
)
from tests.conftest import connected_session

probe = FastMCP("error-boundary-probe")


@probe.tool()
@tool_errors
async def rejects_input() -> str:
    """Raise a taxonomy error the caller could fix."""
    raise InvalidInputError("radiusKm must be between 1 and 40.")


@probe.tool()
@tool_errors
async def times_out() -> str:
    """Raise a taxonomy error that is worth retrying."""
    raise UpstreamTimeoutError("mapeak.com did not respond within 10s.")


@probe.tool()
@tool_errors
async def leaks() -> str:
    """Fail the way real bugs fail: with an exception nobody planned for."""
    raise UpstreamNotFound("https://mapeak.com/api/urls/private-id")


async def error_text(tool: str) -> str:
    async with connected_session(probe) as session:
        result = await session.call_tool(tool, {})

    assert result.isError is True
    (block,) = result.content
    assert isinstance(block, TextContent)
    return block.text


async def test_a_taxonomy_error_reaches_the_client_with_its_code():
    text = await error_text("rejects_input")

    assert "[invalid_input]" in text
    assert "radiusKm must be between 1 and 40." in text


async def test_a_retryable_error_says_so():
    text = await error_text("times_out")

    assert "[upstream_timeout]" in text
    assert "may succeed later" in text


async def test_an_unplanned_exception_is_generic_on_the_wire(
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level(logging.ERROR):
        text = await error_text("leaks")

    assert "[upstream_unavailable]" in text
    assert "private-id" not in text
    assert "Traceback" not in text
    assert "private-id" in caplog.text  # still debuggable from the stderr log


async def test_error_text_is_one_line_no_matter_the_tool():
    for tool in ("rejects_input", "times_out", "leaks"):
        assert "\n" not in await error_text(tool)
