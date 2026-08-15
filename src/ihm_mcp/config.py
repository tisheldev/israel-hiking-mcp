"""Runtime settings, read from the environment once at startup.

Every value here is a promise made to somebody else's server. They are
configurable because deployments differ, and bounded because an unbounded value
is a way to accidentally hammer a volunteer-run service.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, HttpUrl, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from ihm_mcp import __version__

ENV_PREFIX = "IHM_"

USER_AGENT = (
    f"israel-hiking-mcp/{__version__} "
    "(+https://github.com/tisheldev/israel-hiking-mcp; "
    "unofficial non-commercial prototype)"
)


class ConfigurationError(Exception):
    """The environment does not describe a runnable server."""


class Settings(BaseSettings):
    """Environment-driven settings, all prefixed `IHM_`."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, frozen=True, extra="ignore")

    base_url: HttpUrl = HttpUrl("https://mapeak.com")
    #: OpenStreetMap's own API. Route geometry for `source: "OSM"` comes from
    #: here rather than from the map site, which is what its frontend does too —
    #: so this server talks to a second host, under a second usage policy.
    osm_api_url: HttpUrl = HttpUrl("https://api.openstreetmap.org/api/0.6/")
    request_timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 10.0
    user_agent: Annotated[str, Field(min_length=1)] = USER_AGENT
    cache_ttl_seconds: Annotated[int, Field(ge=0, le=86_400)] = 300
    cache_max_entries: Annotated[int, Field(ge=1, le=100_000)] = 512
    max_concurrent_requests: Annotated[int, Field(ge=1, le=16)] = 4
    max_tiles_per_tool_call: Annotated[int, Field(ge=1, le=500)] = 100
    #: A route relation can be built from other relations, each of which is a
    #: request of its own; this bounds one `get_route_details` call.
    max_osm_requests_per_tool_call: Annotated[int, Field(ge=1, le=64)] = 16


def load_settings() -> Settings:
    """Build settings from the environment, or raise `ConfigurationError`."""
    try:
        return Settings()
    except ValidationError as exc:
        # Users set IHM_CACHE_TTL_SECONDS; pydantic reports `cache_ttl_seconds`
        # and sends them looking for the wrong thing.
        problems = [
            f"  {ENV_PREFIX}{error['loc'][0]}".upper() + f": {error['msg']}"
            for error in exc.errors()
        ]
        raise ConfigurationError("\n".join(["invalid configuration:", *problems])) from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings. Tests that change the environment must
    call `get_settings.cache_clear()`."""
    return load_settings()
