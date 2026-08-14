# Israel Hiking MCP

An unofficial, non-commercial, read-only [MCP](https://modelcontextprotocol.io)
server exposing Israel Hiking Map / [Mapeak](https://mapeak.com) hiking data to
LLM hosts.

> **Status: early scaffolding (PR 4 of 9).** The server speaks MCP over stdio,
> has its HTTP foundation — settings, typed errors, a cached and rate-capped
> upstream client — exposes its first real tool, `search_places`, and can read
> map features out of the vector tileset. Route search, route details, POIs
> along a route, and routing all land in later PRs.

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
and call `ping`, then `search_places` with `query: "Haifa"` — the first result
should be Haifa, Israel, with an `ihmUrl` that opens on the map site.

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
| `search_places` | Find places by name (Hebrew or English) and return ranked candidate coordinates, each with a `{source, identifier}` ref and a link to the map site. |
| `ping` | Liveness check returning the server version and data attribution. Placeholder; removed once the tool set is complete. |

### `search_places`

| Argument | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | Place name, 2–100 characters after trimming |
| `israelOnly` | boolean | `true` | Drop matches outside the Israel bounding box |
| `language` | `he` \| `en` | `en` | Language of the returned names, and of the `ihmUrl` link |
| `limit` | integer | `10` | 1–20 |

Upstream's search is a **worldwide** index — "Haifa" matches places in Syria,
the United States, France and Chile before it reaches Haifa, Israel. Results
are therefore ranked by whether they fall inside an approximate bounding box
around Israel (29.3–33.4 N, 34.2–35.9 E), and by default the rest are dropped;
the `warnings` field always says when that happened. The box is a ranking
device, not a border: it is axis-aligned, so it also takes in parts of
neighbouring territory.

No match is not an error — the tool returns an empty `places` list with a
warning explaining what to try next. The tool never picks a candidate for you.

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

### Area searches and the tile budget

The map's routes and points of interest have no query API; they exist only as
the vector tiles the map site draws (`/vector/data/global_points/{z}/{x}/{y}.mvt`,
zoom 10–14). An area search therefore costs one request per tile covering it,
which grows with the square of the radius. Searches run at **zoom 12** by
default, where a 40 km radius costs 98 tiles — just inside the default budget of
100. Only tiles that actually reach into the search circle are fetched; the box
around that circle would cost 121.

Past the budget, a search fails with `search_area_too_large` naming the tile
count, the limit, and a radius that would have worked, rather than silently
returning part of the area.

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
