# Israel Hiking MCP

An unofficial, non-commercial, read-only [MCP](https://modelcontextprotocol.io)
server exposing Israel Hiking Map / [Mapeak](https://mapeak.com) hiking data to
LLM hosts.

> **Status: early scaffolding (PR 2 of 9).** The server speaks MCP over stdio
> and has its HTTP foundation — settings, typed errors, a cached and rate-capped
> upstream client — but still exposes only a placeholder `ping` tool. Place
> search, route search, route details, POIs along a route, and routing all land
> in later PRs.

See [LICENSE-NOTICE.md](LICENSE-NOTICE.md) before using any output — the
upstream data is CC BY-NC-SA 3.0 (non-commercial, share-alike) and ODbL.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
```

## Run

```bash
uv run israel-hiking-mcp
```

The server communicates over stdio and produces no stdout output of its own —
stdout is reserved for the JSON-RPC message stream. Logs go to stderr; set
`IHM_LOG_LEVEL=DEBUG` for verbose output.

## Tests

```bash
uv run pytest
```

The suite runs fully offline.

## Try it with MCP Inspector

```bash
npx @modelcontextprotocol/inspector uv run israel-hiking-mcp
```

Set the working directory to this repository, connect, open the **Tools** tab,
and call `ping`.

## Use from an MCP host

Claude Desktop (`claude_desktop_config.json`) or any generic MCP client:

```json
{
  "mcpServers": {
    "israel-hiking": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/israel-hiking-mcp", "run", "israel-hiking-mcp"]
    }
  }
}
```

## Tools

| Tool | Description |
|---|---|
| `ping` | Liveness check returning the server version and data attribution. Placeholder; removed once real tools exist. |

## Configuration

All settings are environment variables, read once at startup — an invalid value
stops the server with a message naming the variable, rather than failing later
inside a tool call.

| Variable | Default | Range | Purpose |
|---|---|---|---|
| `IHM_BASE_URL` | `https://mapeak.com` | http(s) URL | API and tile host |
| `IHM_REQUEST_TIMEOUT_SECONDS` | `10` | 0 < x ≤ 60 | Applied to connect, read, write and pool |
| `IHM_USER_AGENT` | `israel-hiking-mcp/<version> (+repo url)` | non-empty | Sent on every request |
| `IHM_CACHE_TTL_SECONDS` | `300` | 0–86400 | `0` disables caching |
| `IHM_CACHE_MAX_ENTRIES` | `512` | ≥ 1 | Bounds the in-memory response cache |
| `IHM_MAX_CONCURRENT_REQUESTS` | `4` | 1–16 | Simultaneous upstream connections |
| `IHM_MAX_TILES_PER_TOOL_CALL` | `100` | 1–500 | Tile budget for area searches |
| `IHM_LOG_LEVEL` | `INFO` | log level | Logs go to stderr only |

### Request behaviour

Every upstream request is a GET with a timeout, capped concurrency, and a
short-lived in-memory cache. A request is retried **once**, and only after a
timeout, a connection failure, or a 5xx; 4xx responses are never retried and
failures are never cached. This is a personal-scale tool pointed at a
volunteer-run service — please keep it that way.

### Error codes

Tool failures arrive as MCP errors whose text begins with a stable code:

`invalid_input` · `place_not_found` · `route_not_found` · `unsupported_source` ·
`search_area_too_large` · `geometry_too_large` · `upstream_timeout` ·
`upstream_unavailable` · `upstream_schema_changed` · `rate_limited`

`upstream_timeout`, `upstream_unavailable` and `rate_limited` are transient; the
rest will not be fixed by retrying. Upstream response bodies, URLs and
tracebacks are never included in the message — those go to the stderr log.
