# Israel Hiking MCP

An unofficial, non-commercial, read-only [MCP](https://modelcontextprotocol.io)
server exposing Israel Hiking Map / [Mapeak](https://mapeak.com) hiking data to
LLM hosts.

> **Status: early scaffolding (PR 1 of 9).** The server runs and speaks MCP over
> stdio, but exposes only a placeholder `ping` tool. Place search, route search,
> route details, POIs along a route, and routing all land in later PRs.

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
