"""The command line, which is two flags and a warning.

`--help` and `--version` are the only things this process ever writes to
stdout, and they are the reason it is worth testing that they exit before the
transport starts. A host launches this server without arguments; a person
trying it out is the one who types `--help`, usually after it appeared to hang.
"""

import subprocess
import sys

import pytest

from ihm_mcp import __version__, server
from ihm_mcp.server import build_parser, main


def run(*args: str) -> subprocess.CompletedProcess[str]:
    """The installed entry point, as a user would reach it."""
    return subprocess.run(
        [sys.executable, "-m", "ihm_mcp.server", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_version_prints_the_package_version_and_exits():
    result = run("--version")

    assert result.returncode == 0
    assert result.stdout.strip() == f"israel-hiking-mcp {__version__}"


def test_help_says_it_is_not_an_interactive_program():
    result = run("--help")

    assert result.returncode == 0
    # The one thing somebody running it by hand needs to be told.
    assert "not an interactive program" in result.stdout
    assert "appear to hang" in result.stdout


def test_help_points_at_the_licence_and_the_environment():
    result = run("--help")

    assert "IHM_" in result.stdout
    assert "LICENSE-NOTICE.md" in result.stdout


def test_neither_flag_starts_the_server():
    # The transport logs "starting ... on stdio" to stderr before it blocks.
    for flag in ("--help", "--version"):
        assert "on stdio" not in run(flag).stderr


def test_an_unknown_flag_is_refused_rather_than_ignored():
    result = run("--activity", "Hiking")

    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_the_parser_is_built_without_starting_anything():
    # `build_parser` is separate from `main` so this can be asserted at all.
    assert build_parser().prog == "israel-hiking-mcp"


def test_main_rejects_arguments_it_does_not_know():
    with pytest.raises(SystemExit) as caught:
        main(["--nope"])

    assert caught.value.code == 2


# `configure_logging` replaces every inherited handler, caplog's included, so
# these read the stream it actually writes to rather than the capture fixture.


def test_a_terminal_on_stdin_is_reported_rather_than_left_looking_hung(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    # Stop at the transport: what is under test is what happens before it.
    started: list[str] = []
    monkeypatch.setattr(server.mcp, "run", lambda transport: started.append(transport))

    main([])

    captured = capsys.readouterr()
    assert started == ["stdio"]
    assert "no MCP client is connected" in captured.err
    assert "--help" in captured.err
    # Even the friendly warning stays off the protocol channel.
    assert captured.out == ""


def test_a_pipe_on_stdin_says_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(server.mcp, "run", lambda transport: None)

    main([])

    assert "no MCP client is connected" not in capsys.readouterr().err
