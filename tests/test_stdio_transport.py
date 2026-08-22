"""Drives a real server subprocess over stdio with hand-written JSON-RPC.

The in-memory tests cover protocol semantics. This one exists for the invariant
they cannot see: on stdio, stdout carries the message stream and nothing else.
A stray print or a log handler pointed at stdout corrupts the stream, and this
is the test that catches it.
"""

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass

import pytest
from mcp.types import LATEST_PROTOCOL_VERSION

from ihm_mcp import SERVER_NAME, __version__

REQUESTS = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "stdio-test", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    # A query the tool refuses on sight. Every tool this server has talks to
    # somebody else's host, and a test that drives a real subprocess is no place
    # to reach one — this call runs the whole path and stops at the boundary.
    {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "search_places", "arguments": {"query": "a"}},
    },
]

EXPECTED_IDS = {1, 2, 3}
TIMEOUT_SECONDS = 60


@dataclass
class Session:
    stdout_lines: list[str]
    stderr: str
    returncode: int


def run_server_session() -> Session:
    """Send the exchange, collect every reply, then close stdin to shut down."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "IHM_LOG_LEVEL": "INFO"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ihm_mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
        bufsize=1,
    )

    stderr_chunks: list[str] = []
    drain = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read()))
    drain.daemon = True
    drain.start()

    watchdog = threading.Timer(TIMEOUT_SECONDS, proc.kill)
    watchdog.start()

    stdout_lines: list[str] = []
    try:
        for request in REQUESTS:
            proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()

        # Read replies *before* closing stdin: EOF triggers shutdown, which
        # would cancel an in-flight tool call.
        seen: set[int] = set()
        while seen != EXPECTED_IDS:
            line = proc.stdout.readline()
            if not line:
                break
            stdout_lines.append(line)
            try:
                seen.add(json.loads(line)["id"])
            except (json.JSONDecodeError, KeyError, TypeError):
                pass  # purity is asserted by the caller, not here

        proc.stdin.close()
        stdout_lines.extend(proc.stdout.readlines())
        proc.wait(timeout=30)
    finally:
        watchdog.cancel()
        if proc.poll() is None:  # pragma: no cover - only on a hung server
            proc.kill()
            proc.wait(timeout=10)
        drain.join(timeout=10)

    return Session(
        stdout_lines=[line for line in stdout_lines if line.strip()],
        stderr="".join(stderr_chunks),
        returncode=proc.returncode,
    )


@pytest.fixture(scope="module")
def session() -> Session:
    return run_server_session()


def test_every_stdout_line_is_a_json_rpc_message(session: Session):
    for line in session.stdout_lines:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:  # pragma: no cover - the failure we guard against
            raise AssertionError(f"non-JSON output on the protocol channel: {line!r}") from None
        assert parsed.get("jsonrpc") == "2.0", f"not a JSON-RPC message: {line!r}"


def test_stdio_session_answers_every_request(session: Session):
    by_id = {}
    for line in session.stdout_lines:
        message = json.loads(line)
        if "id" in message:
            by_id[message["id"]] = message

    assert set(by_id) == EXPECTED_IDS
    assert all("error" not in message for message in by_id.values())

    init = by_id[1]["result"]
    assert init["serverInfo"]["name"] == SERVER_NAME
    assert init["serverInfo"]["version"] == __version__
    assert init["capabilities"]["tools"] is not None

    listed = [tool["name"] for tool in by_id[2]["result"]["tools"]]
    assert "route_between_points" in listed

    # A refused call is a result carrying `isError`, not a JSON-RPC error — and
    # the taxonomy code has to survive the real transport, not only the
    # in-memory one.
    call = by_id[3]["result"]
    assert call["isError"] is True
    assert "[invalid_input]" in "".join(block["text"] for block in call["content"])


def test_logs_go_to_stderr_and_shutdown_is_clean(session: Session):
    assert f"starting {SERVER_NAME}" in session.stderr
    # The stderr formatter's signature must never appear on the protocol channel.
    assert "INFO ihm_mcp" not in "".join(session.stdout_lines)
    assert session.returncode == 0, f"unclean shutdown; stderr:\n{session.stderr}"
