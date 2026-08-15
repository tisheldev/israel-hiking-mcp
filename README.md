# Israel Hiking MCP

An unofficial, non-commercial, read-only [MCP](https://modelcontextprotocol.io)
server exposing Israel Hiking Map / [Mapeak](https://mapeak.com) hiking data to
LLM hosts.

> **Status: early scaffolding (PR 7 of 9).** The server speaks MCP over stdio,
> has its HTTP foundation — settings, typed errors, a cached and rate-capped
> upstream client — and exposes three tools: `search_places`,
> `search_hiking_routes` and `get_route_details`, which now resolves both OSM
> routes and user-shared ones. POIs along a route and routing land in later PRs.

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
should be Haifa, Israel, with an `ihmUrl` that opens on the map site. Feed those
coordinates to `search_hiking_routes` with `radiusKm: 15`, `minLengthKm: 4`,
`maxLengthKm: 12` and you should get the Haifa Trail segments and a set of
Nakeb routes, nearest first.

Then take the `ref` of any result and pass it to `get_route_details` as
`route`. An `OSM` one — `{"source": "OSM", "identifier": "relation_13207704"}`
is the Haifa Trail — comes back as the trail's line, assembled from the ways it
is mapped as; a `Users` one comes back with its author's own title, description
and recorded climb. `{"source": "OSM", "identifier": "relation_282071"}` is the
Israel National Trail: 2,480 ways and 41,689 recorded positions, returned as one
continuous line thinned to fit.

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
| `search_hiking_routes` | List hiking routes mapped near a point, nearest first, with length, any difficulty rating, and a link to the map site. |
| `get_route_details` | Resolve one route ref into its GeoJSON line plus the title, description, length, climb and difficulty its source records. Resolves OSM and user-shared routes. |
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

### `search_hiking_routes`

| Argument | Type | Default | Notes |
|---|---|---|---|
| `center` | `{lat, lng}` | — | Point to search around; `search_places` returns this shape |
| `radiusKm` | number | `10` | 1–40, measured to each route's start marker |
| `minLengthKm` | number \| null | `null` | 0–1000 |
| `maxLengthKm` | number \| null | `null` | 0–1000 |
| `difficulty` | `Easy` \| `Moderate` \| `Hard` \| `Very Hard` \| null | `null` | See below |
| `language` | `he` \| `en` | `en` | Language of names, descriptions and the `ihmUrl` link |
| `limit` | integer | `10` | 1–20 |

Only hiking routes are returned. The same tiles carry cycling and 4x4 routes,
which this tool does not search.

Results are **deterministic**: the same centre, radius and constraints produce
the same routes in the same order, sorted by distance from the search centre
and then by `{source, identifier}`. Distances are great-circle, reported to
10 m, and are not walking distances.

Two upstream quirks the tool mirrors deliberately, and reports in `warnings`:

- **Most routes carry no difficulty rating.** Upstream's own route filter keeps
  an unrated route whatever difficulty is asked for, and so does this tool —
  dropping them would empty almost every search. Each such result carries
  `difficulty: null`, and a warning says how many were kept that way.
- **A route the map does not name is not returned.** The map site hides those
  features too; the count of skipped markers appears in `warnings`.

A route with no length in the map data is returned with `lengthKm: null`, and
cannot satisfy a length range.

Nothing in a result establishes that a route is open, marked, passable,
permitted, or safe. `description` is the mapper's own undated note, and is
often the only place a closure or hazard is recorded.

### `get_route_details`

| Argument | Type | Default | Notes |
|---|---|---|---|
| `route` | `{source, identifier}` | — | The `ref` object from a `search_hiking_routes` result |
| `language` | `he` \| `en` | `en` | Preferred language where the source holds both |

Returns the route's line as GeoJSON (`LineString`, or `MultiLineString` when the
route's parts do not join up), with `startPoint`, `title`, `description`,
`activity`, `difficulty`, `lengthKm`, `ascentMeters`, `descentMeters` and a link
to the map site.

**Two sources resolve: `OSM` and `Users`.** `Nakeb`, `iNature` and `Wikidata`
return `unsupported_source` rather than a guessed URL; the `ihmUrl` from the
search result opens any of them on the map site meanwhile.

Geometry is GeoJSON, so its positions are `[longitude, latitude]` — the
opposite order to every other coordinate this server returns.

#### `source: "OSM"`

Identifiers look like `relation_282071`, `way_12345` or `node_67890`, which is
what the search returns. Geometry comes from **`api.openstreetmap.org`
directly**, as the map site's own frontend does — a second host, under
[OSM's usage policy](https://operations.osmfoundation.org/policies/api/), with
the same identifying User-Agent and the same caching as every other request
here.

A route is a relation of ways, each way drawn in whatever direction its mapper
drew it and listed in whatever order the relation holds; ways whose ends
coincide are sewn back into continuous lines. Where the mapped ways genuinely do
not meet, the result is a `MultiLineString` and a warning says so — the Haifa
Trail's downtown section is mapped as ten disconnected pieces. A node is refused
as `invalid_input` without asking OSM: a point cannot be a route.

`/full` returns a relation's members but **not** the members of relations nested
inside it, so a trail split into sections costs one request per section. That
recursion is bounded by `IHM_MAX_OSM_REQUESTS_PER_TOOL_CALL` (16), follows
sections breadth-first, visits each relation once however often it is
referenced, and past the budget fails with `geometry_too_large` rather than
returning part of a trail as if it were all of it.

Everything reported besides the geometry is an OSM tag, so most of it is absent
on most routes: `lengthKm` comes from `distance` only, `ascentMeters` and
`descentMeters` from `ascent`/`descent`, `difficulty` from a `difficulty` tag
that happens to name one of upstream's four grades, and `activity` from `route`
in the map site's own words (`hiking`/`foot` → Hiking, `bicycle`/`mtb` →
Bicycle, `road` + `scenic=yes` → 4x4). The non-standard `length` tag is
deliberately not read — a sampled Haifa Trail section tags `length=3` where the
map's own computed length is 8.59 km. For length, use the `lengthKm` of the
search result that produced the ref.

#### `source: "Users"`

A share stores a route as segments cut at each point its author dropped, each
restating the previous segment's last point. Those are joined into one line and
the repeated junction positions are dropped. A share holding several routes
comes back as a `MultiLineString`. Positions are reported to six decimals, about
0.11 m.

A route of more than **3,000 positions** is thinned by Douglas-Peucker at the
smallest tolerance that brings it under that cap — 10 m, then 25, 50, 100 —
measured in a local equirectangular frame. `geometryDetail` reports
`recordedPointCount`, `pointCount`, `simplified` and `toleranceMeters`, and a
warning says the same in words. Every kept position is one the recorded line
really passed through, and the first and last always survive. A shape still
over the cap at 100 m fails with `geometry_too_large` rather than being
truncated.

Every response carries a fixed `unknowns` list — closure and access status,
water, waymarking and terrain, time required. These are properties of the data,
not of the particular route: the map records where somebody went, not whether
going there is currently a good idea.

## Configuration

All settings are environment variables, read once at startup — an invalid value
stops the server with a message naming the variable, rather than failing later
inside a tool call.

| Variable | Default | Range | Purpose |
|---|---|---|---|
| `IHM_BASE_URL` | `https://mapeak.com` | http(s) URL | API and tile host |
| `IHM_OSM_API_URL` | `https://api.openstreetmap.org/api/0.6/` | http(s) URL | Where OSM route geometry is fetched from |
| `IHM_REQUEST_TIMEOUT_SECONDS` | `10` | 0 < x ≤ 60 | Applied to connect, read, write and pool |
| `IHM_USER_AGENT` | `israel-hiking-mcp/<version> (+repo url)` | non-empty | Sent on every request |
| `IHM_CACHE_TTL_SECONDS` | `300` | 0–86400 | `0` disables caching |
| `IHM_CACHE_MAX_ENTRIES` | `512` | ≥ 1 | Bounds the in-memory response cache |
| `IHM_MAX_CONCURRENT_REQUESTS` | `4` | 1–16 | Simultaneous upstream connections |
| `IHM_MAX_TILES_PER_TOOL_CALL` | `100` | 1–500 | Tile budget for area searches |
| `IHM_MAX_OSM_REQUESTS_PER_TOOL_CALL` | `16` | 1–64 | Request budget for nested OSM relations |
| `IHM_LOG_LEVEL` | `INFO` | log level | Logs go to stderr only |

### Area searches and the tile budget

The map's routes and points of interest have no query API; they exist only as
the vector tiles the map site draws (`/vector/data/global_points/{z}/{x}/{y}.mvt`,
zoom 10–14). An area search therefore costs one request per tile covering it,
which grows with the square of the radius. Searches run at **zoom 12** by
default, where a 40 km radius costs 98 tiles — just inside the default budget of
100. Only tiles that actually reach into the search circle are fetched; the box
around that circle would cost 121.

Zoom 12 was chosen for that budget and then checked for completeness: sampling
zoom-12 tiles over Haifa, Jerusalem, Eilat and the Golan on 2026-08-14 against
all sixteen of each tile's zoom-14 children found **no features dropped** at the
lower zoom (817 features in the densest sample, 0 missing). The tileset is not
thinned by zoom, so a zoom-12 search sees every marker a zoom-14 one would.

Past the budget, a search fails with `search_area_too_large` naming the tile
count, the limit, and a radius that would have worked, rather than silently
returning part of the area.

### Request behaviour

Every upstream request is a GET with a timeout, capped concurrency, and a
short-lived in-memory cache. A request is retried **once**, and only after a
timeout, a connection failure, or a 5xx; 4xx responses are never retried and
failures are never cached. This is a personal-scale tool pointed at a
volunteer-run service — please keep it that way.

Two hosts are contacted, each with its own connection pool and cache:
`mapeak.com` for search, tiles and shared routes, and `api.openstreetmap.org`
for OSM route geometry. Both see the same identifying User-Agent. OSM's
[API usage policy](https://operations.osmfoundation.org/policies/api/) applies
to the second, and one deeply nested relation is the only thing here that can
cost more than a handful of requests — hence its own budget above.

### Error codes

Tool failures arrive as MCP errors whose text begins with a stable code:

`invalid_input` · `place_not_found` · `route_not_found` · `unsupported_source` ·
`search_area_too_large` · `geometry_too_large` · `upstream_timeout` ·
`upstream_unavailable` · `upstream_schema_changed` · `rate_limited`

`upstream_timeout`, `upstream_unavailable` and `rate_limited` are transient; the
rest will not be fixed by retrying. Upstream response bodies, URLs and
tracebacks are never included in the message — those go to the stderr log.
