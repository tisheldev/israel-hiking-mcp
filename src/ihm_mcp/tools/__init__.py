"""Tool implementations.

Importing this package registers every tool on the application, which is why
`server.py` imports it for its side effect rather than for a name.
"""

from ihm_mcp.tools import places as places  # noqa: F401
from ihm_mcp.tools import routes as routes  # noqa: F401
