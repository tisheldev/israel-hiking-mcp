"""Settings are a validated boundary, not a bag of strings."""

import os

import pytest

from ihm_mcp import __version__
from ihm_mcp.config import ConfigurationError, Settings, get_settings, load_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """No IHM_* variable from the developer's shell may reach these tests."""
    for name in list(os.environ):
        if name.startswith("IHM_"):
            monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_defaults_are_conservative():
    settings = load_settings()

    assert str(settings.base_url).startswith("https://mapeak.com")
    assert str(settings.osm_api_url).startswith("https://api.openstreetmap.org/api/0.6")
    assert settings.request_timeout_seconds == 10.0
    assert settings.cache_ttl_seconds == 300
    assert settings.max_concurrent_requests == 4
    assert settings.max_tiles_per_tool_call == 100
    assert settings.max_osm_requests_per_tool_call == 16


def test_user_agent_identifies_the_project_and_version():
    """Upstream admins must be able to tell who this traffic is and contact us."""
    user_agent = load_settings().user_agent

    assert "israel-hiking-mcp" in user_agent
    assert __version__ in user_agent
    assert "https://github.com/" in user_agent


def test_environment_overrides_are_parsed_and_coerced(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("IHM_BASE_URL", "https://staging.example.com")
    monkeypatch.setenv("IHM_REQUEST_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("IHM_MAX_CONCURRENT_REQUESTS", "1")
    monkeypatch.setenv("IHM_CACHE_TTL_SECONDS", "0")

    settings = load_settings()

    assert settings.base_url.host == "staging.example.com"
    assert settings.request_timeout_seconds == 2.5
    assert settings.max_concurrent_requests == 1
    assert settings.cache_ttl_seconds == 0  # 0 is legal: caching off


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("IHM_REQUEST_TIMEOUT_SECONDS", "0"),
        ("IHM_REQUEST_TIMEOUT_SECONDS", "600"),
        ("IHM_REQUEST_TIMEOUT_SECONDS", "soon"),
        ("IHM_MAX_CONCURRENT_REQUESTS", "0"),
        ("IHM_MAX_CONCURRENT_REQUESTS", "500"),
        ("IHM_CACHE_TTL_SECONDS", "-1"),
        ("IHM_CACHE_MAX_ENTRIES", "0"),
        ("IHM_MAX_TILES_PER_TOOL_CALL", "100000"),
        ("IHM_MAX_OSM_REQUESTS_PER_TOOL_CALL", "0"),
        ("IHM_MAX_OSM_REQUESTS_PER_TOOL_CALL", "1000"),
        ("IHM_BASE_URL", "not-a-url"),
        ("IHM_OSM_API_URL", "api.openstreetmap.org"),
        ("IHM_USER_AGENT", ""),
    ],
)
def test_bad_values_are_rejected_and_name_the_variable(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
):
    monkeypatch.setenv(variable, value)

    with pytest.raises(ConfigurationError) as caught:
        load_settings()

    # The message has to point at what the user actually set.
    assert variable in str(caught.value)


def test_settings_are_immutable():
    settings = load_settings()

    with pytest.raises(Exception):
        settings.request_timeout_seconds = 30  # type: ignore[misc]


def test_get_settings_parses_the_environment_once(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("IHM_CACHE_TTL_SECONDS", "11")
    first = get_settings()

    monkeypatch.setenv("IHM_CACHE_TTL_SECONDS", "22")
    assert get_settings() is first
    assert get_settings().cache_ttl_seconds == 11

    get_settings.cache_clear()
    assert get_settings().cache_ttl_seconds == 22


def test_unknown_ihm_variables_are_ignored(monkeypatch: pytest.MonkeyPatch):
    """`IHM_LOG_LEVEL` is read by logging, not by Settings; it must not error."""
    monkeypatch.setenv("IHM_LOG_LEVEL", "DEBUG")

    assert isinstance(load_settings(), Settings)
