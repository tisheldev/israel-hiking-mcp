"""MCP server entry point.

Transport note: on stdio, stdout *is* the JSON-RPC channel. Anything written
there that is not a protocol message corrupts the stream and breaks the client,
so this process never prints to stdout — logging is pinned to stderr. The one
exception is `--help` and `--version`, which print and exit before the
transport is ever started.

The application object lives in `app.py`; this module wires up logging, pulls
in the tool package so its tools register, and starts the transport.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from ihm_mcp import SERVER_NAME, __version__
from ihm_mcp import tools as tools  # registers the tools
from ihm_mcp.app import mcp
from ihm_mcp.config import ConfigurationError, get_settings

logger = logging.getLogger("ihm_mcp")

__all__ = ["build_parser", "configure_logging", "main", "mcp"]

#: Shown by `--help`. A server that answers nothing when run by hand is the
#: normal case for stdio, and the first thing somebody trying it needs told.
DESCRIPTION = """An unofficial, non-commercial, read-only MCP server for Israel Hiking Map
(Mapeak) and OpenStreetMap hiking data.

This is not an interactive program. It speaks the Model Context Protocol over
stdin and stdout, and is meant to be launched by an MCP host such as Claude
Desktop or Claude Code rather than run by hand. Started in a terminal it will
appear to hang: it is waiting for a client that is never going to say anything.
"""

EPILOG = """configuration:
  Every setting is an environment variable prefixed IHM_, read once at
  startup. IHM_LOG_LEVEL=DEBUG turns on verbose logging, which goes to
  stderr. The full table is in the README.

licensing:
  Upstream data is CC BY-NC-SA 3.0 and ODbL. Anything this server returns is
  non-commercial and share-alike, and carries an attribution that has to be
  preserved. See LICENSE-NOTICE.md.

homepage:
  https://github.com/tisheldev/israel-hiking-mcp
"""


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


def build_parser() -> argparse.ArgumentParser:
    """The command line, which exists mostly so `--help` can explain that there
    isn't one. Every knob is an environment variable."""
    return argparse.ArgumentParser(
        prog="israel-hiking-mcp",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.parse_args(argv)

    configure_logging()
    if sys.stdin.isatty():
        # Nothing is wrong, and nothing is about to happen either. Say so
        # rather than let it look like a hang.
        logger.warning(
            "stdin is a terminal, so no MCP client is connected. This server "
            "is meant to be launched by an MCP host; run with --help for how. "
            "Waiting for protocol messages on stdin; Ctrl-C to stop."
        )
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
