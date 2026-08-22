# Contributing

This is a personal, portfolio-scale project pointed at services run by
volunteers. That shapes most of what follows, so please read
[Responsible use](README.md#responsible-use) before opening anything.

## Reporting a bug

Open an [issue](https://github.com/tisheldev/israel-hiking-mcp/issues). The
template asks for four things, and the report is usually unactionable without
them:

- **which tool**, and the **arguments** you called it with;
- **what came back** — the error code, or the part of the response that was
  wrong;
- **the stderr log**. Nothing this server prints goes to stdout, so the log is
  the only place a cause is recorded. Set `IHM_LOG_LEVEL=DEBUG` and include it;
- **`israel-hiking-mcp --version`**, and how you installed it.

Please redact nothing but your own coordinates if you would rather not share
them — the rest of the log is upstream URLs and timings, and the server never
logs credentials because it never has any.

One thing that is **not** a bug: a trail that has closed, a spring that has
dried, a route that is mapped wrongly. This server reports what the map says.
Fixing the map is done [upstream](https://www.openstreetmap.org), and doing so
is worth far more than an issue here.

## How it got this way

[docs/implementation-plan.md](docs/implementation-plan.md) is the plan the nine
pull requests were built from, annotated after each one with what the build
actually turned up — including the parts where live behaviour contradicted the
plan. It is the fastest way to understand why a decision here is the way it is
before proposing a different one.

## Setting up

```bash
uv sync
```

## The checks

All three run in CI on every pull request, across Python 3.11–3.13 on Linux and
Windows. Run them before pushing:

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

`ruff format` is deliberately **not** enforced, and please do not run it over
the repository. It rejoins lines that are split by hand for readability, and at
every line length tried it reformats between fifteen and twenty-six files.
`line-length = 96` is a ceiling, not a target.

mypy is `strict` over `src`, which is clean and should stay that way. Tests are
checked too, but are not required to annotate `def test_...() -> None`.

## The live tests

```bash
uv run pytest -m live
```

Deselected by default, and they should stay that way. They talk to
`mapeak.com` and `api.openstreetmap.org` — about a dozen requests — and they
assert *contract* rather than content: that a route comes back with an
identity, a name, a link and its cautions, not which routes are near Haifa
today. A weekly scheduled run is what notices upstream moving.

Do not add a live test to cover something a fixture can cover, and do not raise
the schedule's frequency.

## Adding a tool

The shape is set by the four that exist. A new one is expected to:

- be **read-only**, and reachable with GET requests only;
- carry `attribution` and an `unknowns` list on every response, and a `caution`
  wherever somebody might act on the answer;
- **refuse rather than guess** — an unrecognised upstream shape is
  `upstream_schema_changed`, not an optimistic read;
- **bound its cost** upstream, and fail with a named limit rather than silently
  return part of an area;
- be tested **offline**, against fixtures in the shape a live response was
  observed in. No map data is committed, because none of it is this project's
  to redistribute — tile fixtures are built in-test with
  `mapbox-vector-tile`'s own encoder.

## Writing

The prose in this repository — docstrings, comments, warnings, error messages —
is part of the work rather than decoration around it. A tool docstring is read
by a language model, and a warning is read by somebody deciding whether to walk
somewhere. Both are worth the same care as the code.

Two habits worth keeping: say what something does **not** establish, and date
any number measured against live data. Two numbers in the README had already
gone stale within a day of being written down.

## Commits and pull requests

Conventional-commit prefixes (`feat:`, `fix:`, `docs:`, `chore:`, `ci:`), and a
body that says **why**. Add a `## [Unreleased]` entry in
[CHANGELOG.md](CHANGELOG.md) for anything that changes the tool contract, the
error codes, or how the server is installed.
