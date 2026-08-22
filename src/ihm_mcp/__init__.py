"""Israel Hiking MCP — an unofficial, non-commercial, read-only MCP server."""

import warnings

# Recent pydantic-settings warns when it walks FastMCP's own `Settings` class,
# whose `lifespan` field is annotated with a forward reference it cannot
# resolve. Nothing here reads that field, and the warning is not actionable by
# anyone installing this server — but it lands on stderr at startup, which for
# a stdio server is the only channel a user sees.
#
# The filter is installed here rather than in `server.py` because the warning
# fires while `app.py` imports FastMCP, and importing any `ihm_mcp.*` module
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

SERVER_NAME = "israel-hiking"

ATTRIBUTION = (
    "Data from Israel Hiking Map / Mapeak (https://mapeak.com), licensed "
    "CC BY-NC-SA 3.0, and from OpenStreetMap contributors "
    "(https://www.openstreetmap.org/copyright), licensed ODbL. "
    "This is an unofficial, non-commercial, read-only prototype and is not "
    "affiliated with or endorsed by the Israel Hiking Map project."
)

__all__ = ["ATTRIBUTION", "SERVER_NAME", "__version__"]
