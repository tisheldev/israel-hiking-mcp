"""Runtime settings, read from the environment once at startup.

Every value here is a promise made to somebody else's server: how long we are
willing to wait, how many connections we open at once, how much we re-ask for
data we already have. They are configurable because deployments differ, and
bounded because an unbounded value is a way to accidentally hammer a
volunteer-run service.

Configuration is parsed and validated *at startup*, not on first use. A typo in
`IHM_REQUEST_TIMEOUT_SECONDS` should stop the server with a readable message,
not surface as a confusing tool failure an hour into a session.
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
    """The environment does not describe a runnable server.

    Deliberately not part of the tool-error taxonomy in `errors.py`: those
    describe a failed request, this one means the process should not start.
    """


class Settings(BaseSettings):
    """Environment-driven settings, all prefixed `IHM_`.

    Frozen: settings are read once and shared by every request, so a mutation
    anywhere would silently change behaviour everywhere.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        frozen=True,
        extra="ignore",
    )

    base_url: HttpUrl = Field(
        default=HttpUrl("https://mapeak.com"),
        description="Israel Hiking Map / Mapeak API and tile host.",
    )
    request_timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 10.0
    user_agent: Annotated[str, Field(min_length=1)] = USER_AGENT
    cache_ttl_seconds: Annotated[int, Field(ge=0, le=86_400)] = 300
    cache_max_entries: Annotated[int, Field(ge=1, le=100_000)] = 512
    max_concurrent_requests: Annotated[int, Field(ge=1, le=16)] = 4
    max_tiles_per_tool_call: Annotated[int, Field(ge=1, le=500)] = 100


def _describe(error: ValidationError) -> str:
    """Turn pydantic's field-oriented report into env-var-oriented advice.

    Users set `IHM_CACHE_TTL_SECONDS`; they never see the field name, so an
    unmodified pydantic message sends them looking for the wrong thing.
    """
    lines = []
    for detail in error.errors():
        field = ".".join(str(part) for part in detail["loc"])
        lines.append(f"  {ENV_PREFIX}{field.upper()}: {detail['msg']}")
    return "invalid configuration:\n" + "\n".join(lines)


def load_settings() -> Settings:
    """Build settings from the environment, or raise `ConfigurationError`."""
    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigurationError(_describe(exc)) from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, parsed on first call.

    Cached so that the environment is read exactly once; tests that manipulate
    the environment must call `get_settings.cache_clear()`.
    """
    return load_settings()
