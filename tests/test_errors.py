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
# and a diff here should be a deliberate decision, not a side effect.
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


def test_the_registry_matches_the_documented_taxonomy():
    assert set(IhmError.registry) == EXPECTED_CODES


@pytest.mark.parametrize("code", sorted(EXPECTED_CODES))
def test_every_error_renders_its_code_first(code: str):
    """`str(exc)` is the entire payload the client receives; the code leads it."""
    error = IhmError.registry[code]("something went wrong")

    assert str(error).startswith(f"[{code}] ")


def test_messages_are_a_single_short_line():
    noisy = "line one\n  line two\t\tline three " + "x" * 500
    error = InvalidInputError(noisy)

    assert "\n" not in error.message
    assert "\t" not in error.message
    assert len(error.message) <= MAX_MESSAGE_CHARS


def test_retryable_errors_say_so_and_others_stay_quiet():
    assert "may succeed later" in str(UpstreamTimeoutError("timed out"))
    assert "may succeed later" not in str(InvalidInputError("radiusKm must be 1-40"))


def test_an_explicit_hint_replaces_the_default():
    error = UpstreamTimeoutError("timed out", hint="Try a smaller radius.")

    assert str(error).endswith("Try a smaller radius.")


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

    # The client sees a generic failure...
    assert secret not in str(caught.value)
    assert "exploding" in str(caught.value)
    # ...while the detail needed to debug it is in the stderr log.
    assert secret in caplog.text
    assert "KeyError" in caplog.text


def test_tool_errors_wraps_sync_functions_too():
    @tool_errors
    def exploding():
        raise ValueError("boom")

    with pytest.raises(UpstreamUnavailableError):
        exploding()


async def test_tool_errors_preserves_the_wrapped_signature():
    @tool_errors
    async def search(query: str, limit: int = 5) -> str:
        """Docstring the MCP layer turns into a tool description."""
        return f"{query}:{limit}"

    # FastMCP builds the input schema and description from these, so the
    # decorator must be transparent.
    assert await search("haifa") == "haifa:5"
    assert search.__name__ == "search"
    assert search.__doc__ is not None
    assert "tool description" in search.__doc__
