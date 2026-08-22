"""MCP server entry point.

Transport note: on stdio, stdout *is* the JSON-RPC channel. Anything written
there that is not a protocol message corrupts the stream and breaks the client,
so this process never prints to stdout — logging is pinned to stderr.

The application object lives in `app.py`; this module wires up logging, pulls
in the tool package so its tools register, and starts the transport.
"""

from __future__ import annotations

import logging
import os
import sys

from ihm_mcp import SERVER_NAME, __version__
from ihm_mcp import tools as tools  # registers the tools
from ihm_mcp.app import mcp
from ihm_mcp.config import ConfigurationError, get_settings

logger = logging.getLogger("ihm_mcp")

__all__ = ["configure_logging", "main", "mcp"]


def configure_logging(level: int | str | None = None) -> None:
    """Send all log records to stderr, replacing any inherited handlers."""
    resolved = level if level is not None else os.getenv("IHM_LOG_LEVEL", "INFO")
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(resolved)


def main() -> None:
    configure_logging()
    try:
        get_settings()
    except ConfigurationError as exc:
        # Fail here rather than as a baffling tool error mid-session.
        logger.error("%s", exc)
        raise SystemExit(2) from None
    logger.info("starting %s v%s on stdio", SERVER_NAME, __version__)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
