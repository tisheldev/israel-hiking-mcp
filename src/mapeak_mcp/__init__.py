"""Mapeak MCP — an unofficial MCP server for Mapeak (formerly Israel Hiking Map):
hiking places, routes and points of interest for LLM hosts.

Unofficial, read-only, and non-commercial in keeping with the upstream data
licence; see LICENSE-NOTICE.md.
"""

import warnings

# Recent pydantic-settings warns when it walks FastMCP's own `Settings` class,
# whose `lifespan` field is annotated with a forward reference it cannot
# resolve. Nothing here reads that field, and the warning is not actionable by
# anyone installing this server — but it lands on stderr at startup, which for
# a stdio server is the only channel a user sees.
#
# The filter is installed here rather than in `server.py` because the warning
# fires while `app.py` imports FastMCP, and importing any `mapeak_mcp.*` module
# runs this file first. It is matched on the exact message so that a real
# warning about this project's own settings still gets through.
#
# Remove once https://github.com/modelcontextprotocol/python-sdk resolves the
# annotation, or once pydantic-settings stops reporting it.
warnings.filterwarnings(
    "ignore",
    message=r"Field 'lifespan' has an incomplete definition",
    category=UserWarning,
)

__version__ = "0.1.0"

SERVER_NAME = "mapeak"

ATTRIBUTION = (
    "Data from Mapeak, formerly Israel Hiking Map (https://mapeak.com), "
    "licensed CC BY-NC-SA 3.0, and from OpenStreetMap contributors "
    "(https://www.openstreetmap.org/copyright), licensed ODbL. "
    "Served by Mapeak MCP, an unofficial, non-commercial, read-only "
    "server that is not run or supported by the Mapeak team."
)

__all__ = ["ATTRIBUTION", "SERVER_NAME", "__version__"]
