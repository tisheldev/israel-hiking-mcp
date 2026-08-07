"""The taxonomy is a contract with the model, so it is tested like one."""

import logging

import pytest

from ihm_mcp.errors import (
    MAX_MESSAGE_CHARS,
    IhmError,
    InvalidInputError,
    UpstreamNotFound,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
    tool_errors,
)

# The PRD's taxonomy, spelled out rather than derived: this list is the spec,
# and a diff here should be a decision, not a side effect.
EXPECTED_CODES = {
    "invalid_input",
    "place_not_found",
    "route_not_found",
    "unsupported_source",
    "search_area_too_large",
    "geometry_too_large",
    "upstream_timeout",
    "upstream_unavailable",
    "upstream_schema_changed",
    "rate_limited",
}

ERRORS = {cls.code: cls for cls in IhmError.__subclasses__()}


def test_the_taxonomy_is_exactly_what_is_documented():
    assert set(ERRORS) == EXPECTED_CODES
    assert len(IhmError.__subclasses__()) == len(EXPECTED_CODES)


@pytest.mark.parametrize("code", sorted(EXPECTED_CODES))
def test_every_error_renders_its_code_first(code: str):
    assert str(ERRORS[code]("something went wrong")).startswith(f"[{code}] ")


def test_messages_are_a_single_short_line():
    error = InvalidInputError("line one\n  line two\t\tline three " + "x" * 500)

    assert "\n" not in error.message
    assert "\t" not in error.message
    assert len(error.message) <= MAX_MESSAGE_CHARS


def test_retryable_errors_say_so_and_others_stay_quiet():
    assert "may succeed later" in str(UpstreamTimeoutError("timed out"))
    assert "may succeed later" not in str(InvalidInputError("radiusKm must be 1-40"))


def test_an_explicit_hint_replaces_the_default():
    assert str(UpstreamTimeoutError("timed out", hint="Try a smaller radius.")).endswith(
        "Try a smaller radius."
    )


def test_upstream_not_found_is_outside_the_taxonomy():
    """It is an internal signal; a tool must translate it before returning."""
    assert not issubclass(UpstreamNotFound, IhmError)


async def test_tool_errors_passes_taxonomy_errors_through_unchanged():
    @tool_errors
    async def failing():
        raise InvalidInputError("limit must be 1-20")

    with pytest.raises(InvalidInputError) as caught:
        await failing()

    assert str(caught.value) == "[invalid_input] limit must be 1-20"


async def test_tool_errors_hides_unexpected_failures(caplog: pytest.LogCaptureFixture):
    secret = "https://mapeak.com/api/search/internal?token=abc"

    @tool_errors
    async def exploding():
        raise KeyError(secret)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(UpstreamUnavailableError) as caught:
            await exploding()

    assert secret not in str(caught.value)
    assert "exploding" in str(caught.value)
    assert secret in caplog.text  # still debuggable from the stderr log
    assert "KeyError" in caplog.text


async def test_tool_errors_preserves_the_wrapped_signature():
    """FastMCP builds the schema and description from these."""

    @tool_errors
    async def search(query: str, limit: int = 5) -> str:
        """Docstring the MCP layer turns into a tool description."""
        return f"{query}:{limit}"

    assert await search("haifa") == "haifa:5"
    assert search.__name__ == "search"
    assert search.__doc__ is not None
    assert "tool description" in search.__doc__
