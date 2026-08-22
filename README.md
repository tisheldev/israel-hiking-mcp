# Israel Hiking MCP

An unofficial, non-commercial, read-only [MCP](https://modelcontextprotocol.io)
server exposing Israel Hiking Map / [Mapeak](https://mapeak.com) hiking data to
LLM hosts.

It answers five questions: where a place is, what hiking routes are mapped near
it, what one of those routes actually looks like, what the map draws beside it,
and how to get from one point to another. Every answer carries its provenance,
what it does not establish, and — where somebody might act on it — an explicit
caution.

> **Status: MVP complete (PR 9 of 9).** Five tools, a fully offline test suite,
> and an opt-in live one. This is a personal, portfolio-scale project pointed at
> a volunteer-run service; read [Responsible use](#responsible-use) before
> pointing anything automated at it.

See [LICENSE-NOTICE.md](LICENSE-NOTICE.md) before using any output — the
upstream data is CC BY-NC-SA 3.0 (non-commercial, share-alike) and ODbL.

## What it will not do

Worth knowing before the tool list, because it is most of what makes this
server trustworthy:

- It never claims a route is **open, permitted, passable or safe**. Nothing in
  the data says so, and every response carries an `unknowns` list saying which
  questions it cannot answer.
- It never treats a **mapped water feature** as water. A spring on the map is a
  record that somebody once saw water there, undated, in a country where most
  water is seasonal — so every water result carries a caution of its own.
- It never **picks for you**. Searches return ranked candidates and say what
  they dropped; nothing is auto-selected.
- It never **guesses at upstream**. A source it cannot resolve is refused by
  name, and a response in a shape it does not recognise is reported as
  `upstream_schema_changed` rather than read optimistically.
- It never **writes anything**, anywhere. Every request it makes is a GET.

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

The suite runs **fully offline**: every upstream response is either a fixture in
the shape a live one was observed in, or a vector tile built in-test with
`mapbox-vector-tile`'s own encoder. No map data is committed, because none of it
is this project's to redistribute.

A second, opt-in group talks to the real hosts and is deselected by default:

```bash
uv run pytest -m live
```

That is the run which catches what a fixture cannot — a renamed field, a moved
endpoint, a routing type upstream stopped recognising. It asserts contract
rather than content (that a route comes back with an identity, a name, a link
and its cautions — not *which* routes are near Haifa today), and costs about a
dozen requests. See [tests/test_live.py](tests/test_live.py).

## Use from an MCP host

**Claude Desktop** — `claude_desktop_config.json`, then restart the app:

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

**Claude Code** — the same server, added from the command line:

```bash
claude mcp add israel-hiking -- uv --directory /absolute/path/to/israel-hiking-mcp run israel-hiking-mcp
```

**Any other MCP client** takes the same three things: the command `uv`, the
arguments above, and stdio transport. The path must be absolute — the host does
not run in this directory. Environment variables from the
[Configuration](#configuration) table can be passed in an `"env"` object beside
`"command"`.

## Try it with MCP Inspector

```bash
npx @modelcontextprotocol/inspector uv run israel-hiking-mcp
```

Set the working directory to this repository, connect, and open the **Tools**
tab. Calling the five in order walks the whole server:

1. `search_places` with `query: "Haifa"` → Haifa, Israel first, with an `ihmUrl`
   that opens on the map site, and a warning saying how many worldwide matches
   were dropped.
2. `search_hiking_routes` with those coordinates, `radiusKm: 15`,
   `minLengthKm: 4`, `maxLengthKm: 12` → Haifa Trail sections, Nakeb routes and
   shared routes, nearest first.
3. `get_route_details` with the `ref` of any of them.
   `{"source": "OSM", "identifier": "relation_13207704"}` is the Haifa Trail,
   assembled from the ways it is mapped as; a `Users` ref comes back with its
   author's own title, description and recorded climb;
   `{"source": "OSM", "identifier": "relation_282071"}` is the Israel National
   Trail — 2,480 ways and 41,689 recorded positions, returned as one continuous
   line thinned to fit; and `{"source": "Nakeb", "identifier": "255"}` is
   refused as `unsupported_source`.
4. `find_pois_along_route` with the Haifa Trail ref, `bufferMeters: 1000` and
   `categories: ["Water"]` → three water features, each with the same `caution`,
   under a warning saying not to plan water on any of them. The Israel National
   Trail's ref shows the tile budget refusing a corridor too long to scan.
5. `route_between_points` between two points on the Carmel → a calculated line,
   with the warning that nobody has walked it.

The [acceptance run](#acceptance-run) below is that walk, with its output.

## Tools

| Tool | Description |
|---|---|
| `search_places` | Find places by name (Hebrew or English) and return ranked candidate coordinates, each with a `{source, identifier}` ref and a link to the map site. |
| `search_hiking_routes` | List hiking routes mapped near a point, nearest first, with length, any difficulty rating, and a link to the map site. |
| `get_route_details` | Resolve one route ref into its GeoJSON line plus the title, description, length, climb and difficulty its source records. Resolves OSM and user-shared routes. |
| `find_pois_along_route` | List the springs, caves, viewpoints and other mapped points within a given distance of a route's line, nearest first, each with its distance in metres. |
| `route_between_points` | Calculate a path between two points for walking, cycling or 4x4, using the map site's own routing engine. The one tool here whose answer is computed rather than recorded. |

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
not meet, the result is a `MultiLineString` and a warning says so — the whole
Haifa Trail came back as 24 disconnected pieces on 2026-08-15, which is the
mapping and not a failure to join it. A node is refused as `invalid_input`
without asking OSM: a point cannot be a route.

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

### `find_pois_along_route`

| Argument | Type | Default | Notes |
|---|---|---|---|
| `route` | `{source, identifier}` | — | The same ref `get_route_details` takes |
| `bufferMeters` | number | `500` | 25–2000, straight-line distance to the route's drawn line |
| `categories` | list of category \| null | `null` | Defaults to Water, Natural, Historic, Viewpoint, Camping |
| `language` | `he` \| `en` | `en` | Language of names, descriptions and the `ihmUrl` link |
| `limit` | integer | `20` | 1–50 |

Resolves the route through the same adapters as `get_route_details`, then
returns the map's points of interest within `bufferMeters` of its line, nearest
first. Each carries `category`, `subtype`, `coordinates`,
`distanceFromRouteMeters` and a link to the map site.

**Water results are the ones people act on, and the ones to be careful with.**
A spring, cistern, waterhole or pool here is a feature somebody recorded on a
map at an unrecorded date. Nothing establishes that it is flowing, reachable,
permitted or safe to drink, and most water in this country is seasonal. Every
water result carries that in a `caution` field of its own — not only in
`warnings`, so that a summary keeping the points and dropping the warnings still
carries it — and a warning repeats it at the head of the list.

Distances are straight lines from the route's **recorded** line, unthinned: this
tool returns numbers about a route rather than the route itself, so nothing is
simplified first. They are not walking distances, and say nothing about whether
anything leads there — a spring 80 m off the trail may be 80 m down a cliff.

**Categories are upstream's, not this server's.** The plan for this tool was to
port the map site's `osm-tags.service.ts` mapping from raw OSM tags to
categories; a live zoom-12 tile settles it differently. Sampled over Haifa on
2026-08-15, every feature carried `poiCategory`, `poiIcon` and `poiIconColor`
and **not one raw OSM tag** — the tag mapping has already run by the time the
tileset is cut. So the category is read, and only the naming is ported:
`poiIcon` back to the label the map site puts on that icon in its own POI
category list. `subtype` is null for an icon that list does not name, including
`icon-search`, upstream's fallback for a feature it recognised only well enough
to draw.

| Category | Subtypes |
|---|---|
| `Water` | Spring, Pond · Waterfall · Waterhole · Water Well · Cistern · Stream, River |
| `Natural` | Cave · Tree · Flowers · Peak, Ridge, Valley |
| `Historic` | Ruins · Archaeological Site · Memorial |
| `Viewpoint` | Viewpoint |
| `Camping` | Picnic Area · Campsite · Alpine Hut |
| `Other` | Place · Attraction · Artwork · Synagogue · Church · Mosque · Place of Worship · Accommodation · Nature Reserve, National Park · iNature Entry · Wikipedia Entry |

`Other` is upstream's bucket for whatever its mapping did not place, and it
outnumbers everything else in a built-up area — 111 of 168 features in a
sampled Haifa tile. It is therefore excluded unless asked for. The route
categories (`Hiking`, `Bicycle`, `4x4`) share those tiles and are never
returned here; `search_hiking_routes` is where they belong.

A **stream or river** is mapped as a line and marked at one point of it, so its
distance is to that mark rather than to wherever the route crosses the water.
When one is in the result, a warning says so.

What a scan costs is set by **how long the route is**, not by the buffer: the
corridor is fetched as map tiles, which are kilometres across. A local trail
costs one or two; the Israel National Trail's corridor cost 128 tiles at zoom 12
when it was measured on 2026-08-15, over the budget of 100, and fails with
`search_area_too_large` saying that a narrower buffer would still need 119 —
resolve one of its sections instead.

An empty list means nothing of that kind is **mapped** near this route. It is
not evidence that there is no water, and every response says so in `unknowns`.

### `route_between_points`

| Argument | Type | Default | Notes |
|---|---|---|---|
| `start` | `{lat, lng}` | — | Where to start; `search_places` returns this shape |
| `end` | `{lat, lng}` | — | Where to finish |
| `activity` | `Hiking` \| `Bicycle` \| `4x4` | `Hiking` | Each uses a different set of ways |

**This is the one tool here whose answer is a computation rather than a
record.** Everything else this server returns is something a person put on a
map. This is a line a routing engine drew just now by joining ways in a graph:
nobody has walked it, and nothing in it knows about a gate, a fence, a firing
zone or a stream in flood. Every response leads with a warning saying so, and it
is not conditional on anything.

Returns the path as a GeoJSON `LineString`, its `lengthKm` measured along the
line, the `straightLineKm` between the two points for comparison, and both ends
as `{requested, onPath, metersApart}` — because **a path starts where the router
can, not where it was asked to**. The ends are snapped to the nearest mapped
way, which in open country can be hundreds of metres away, and covering that
ground is a walk the path does not include. Past 100 m either end says so in
`warnings` too.

Upstream sends an elevation with every position. It is dropped: it is
interpolated from a profile sampled every 30 m, and the numbers worth computing
from it — total climb, above all — would be noise dressed as data.

Bounds: both points must be inside the area the map covers (about 29.3–33.4 N,
34.2–35.9 E, the same box `search_places` ranks by), at least 10 m apart, and no
more than **100 km** apart in a straight line. Each of those is refused as
`invalid_input` before any request is sent — the router's graph stops where the
map does, and a live call from Haifa into the sea was still unanswered after
40 seconds.

**`activity` is a closed set of three, deliberately.** Upstream's `type=` takes
`Hike`, `Bike` and `4WD`, and answers anything else — a typo, a renamed
constant — with a *walking route* rather than an error. So this server sends
only words it knows, and then checks the profile upstream says it used against
the one asked for: a cycling request that comes back as a footpath is reported
as `upstream_schema_changed` rather than returned. The live test group exercises
that check on every activity, which is the only place it can be exercised.

`None` — upstream's fourth routing type, a straight line — is **not offered**. A
live call answers HTTP 500 (checked twice, 2026-08-15), and the map site's own
frontend never sends it either: it interpolates the straight line client-side.
Calling one a route would be the most misleading thing this server could return.

There is no `ihmUrl`: a calculated path is not a feature on the map site, and
this server creates nothing upstream to link to.

## Known limitations

Things that are true of this server by design, and worth knowing before trusting
an answer:

- **Everything is a mapped fact, not a current one.** The data carries no
  observation dates. A trail may have closed, a spring dried, a track been
  fenced, at any point since somebody typed it in.
- **Only hiking routes are searchable.** The same tiles carry cycling and 4x4
  routes; `search_hiking_routes` does not return them.
- **Two of the map's sources resolve to geometry.** `OSM` and `Users` do;
  `Nakeb`, `iNature` and `Wikidata` appear in search results and are refused by
  `get_route_details` with `unsupported_source`. Their `ihmUrl` still opens on
  the map site.
- **Area searches are bounded by a tile budget**, so a 40 km radius is the
  largest circle and a country-length route is too long a corridor to scan.
- **Long geometries are thinned**, and a route still too detailed at 100 m is
  refused rather than truncated.
- **Names come in one language or the other**, whichever the map data holds;
  `language` is a preference, not a translation.
- **Nothing is cached across restarts.** The cache is in memory, and a five
  minute TTL by default.
- **No elevation, no duration, no turn-by-turn.** The routing endpoint can
  return instructions; this server does not ask for them.

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

The same budget covers the corridor along a route, where the shape is a line
rather than a circle and only the tiles the line itself comes near are fetched —
the box around a route running diagonally holds more than twice as many.

Past the budget, a search fails with `search_area_too_large` naming the tile
count, the limit, and either a radius that would have worked or, for a route too
long for any buffer, that fact. It never silently returns part of the area.

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

## Acceptance run

The Haifa scenario, driven through a real MCP session against the live hosts on
**2026-08-15**. Abridged — warnings and attribution are on every response.

```
search_places "Haifa"
  Haifa | Haifa, Haifa Subdistrict, Israel | /poi/OSM/node_1656107649
  warning: 10 match(es) outside Israel were omitted.

search_hiking_routes  15 km around it, 4–12 km long
   1.23 km  OSM/relation_13363223    8.59 km  Haifa Trail - Upper Hadar and Ramat Hadar
   1.75 km  Nakeb/255                 7.5 km  טיול מדרגות חיפה
   5.21 km  OSM/relation_20976850    5.87 km  Connecting Paths
   7.18 km  Users/iXKu2M4BV5         6.46 km  נחל גלים, כלח והמבצר האחרון
   …
  warning: Showing the 10 nearest of 16 matching routes.

get_route_details  OSM/relation_13207704
  Haifa Trail | Hiking | 903 positions, not thinned | MultiLineString
  warning: recorded as 24 separate lines that do not meet end to end

get_route_details  Users/iXKu2M4BV5
  נחל גלים, כלח והמבצר האחרון | 6.46 km | +256 / −251 m | 538 positions

find_pois_along_route  the Haifa Trail, 1,000 m, Water
    120 m  Water Well     Vardiya Spring Well
    615 m  Waterfall      מפל נחל הגיבורים
    960 m  Spring, Pond   בריכת מעיין התאנה
  warning: 3 of these are water features. Mapped water feature — not known to
           be flowing, reachable, permitted or safe to drink. …

find_pois_along_route  the same route, 500 m, default categories
    106 m  Natural   Cave        מערה כפולה
    120 m  Water     Water Well  Vardiya Spring Well
    443 m  Historic  Memorial    גדוד 22 חטיבת "כרמלי"

route_between_points  32.8191,34.9984 → 32.7800,35.0200 (straight line 4.79 km)
  Hiking    6.39 km, 367 positions, ends snapped 11 m / 5 m
  Bicycle   7.84 km, 391 positions, ends snapped 33 m / 5 m
  4x4       8.01 km, 417 positions, ends snapped 33 m / 5 m

refusals
  route_between_points into Cyprus     → [invalid_input] outside the area this map covers
  route_between_points Haifa → Eilat   → [invalid_input] 364 km apart; routes up to 100 km
  find_pois_along_route on the INT     → [search_area_too_large] 128 tiles; a narrower
                                         buffer would still need 119
```

Two things the run showed that had already changed upstream since they were
first measured: the Haifa Trail now resolves to 24 disconnected lines rather
than the ten counted for its downtown section on 2026-08-14, and the Israel
National Trail's corridor now costs 128 tiles rather than 123. Both are the
mapping moving, not the server — and both are why the numbers in this README
carry the date they were measured on.

## Licensing and attribution

Read [LICENSE-NOTICE.md](LICENSE-NOTICE.md) in full before using any output.
The short version:

- Upstream map data is **CC BY-NC-SA 3.0** (Israel Hiking Map / Mapeak) and
  **ODbL** (OpenStreetMap). The upstream licence states that all output of the
  work carries the same licence, so **anything this server returns is
  non-commercial and share-alike**.
- Every response carries an `attribution` object naming both sources and their
  licences. **Preserve it** wherever the data is displayed or passed on. A model
  handed data with no provenance will invent some.
- This project is **unofficial**. It is not affiliated with, endorsed by, or
  supported by the Israel Hiking Map project or its maintainers.
- This repository's own code is distributed under the same CC BY-NC-SA 3.0
  terms, to keep the combined work unambiguous. The full legal text is in
  [LICENSE](LICENSE).

## Responsible use

Both hosts this server talks to are run for the public by people who are not
being paid to serve it. Everything below is a deliberate constraint, not an
accident of implementation:

- **Read-only.** Every request is a GET. Nothing is written, no account is
  created, no session is held.
- **Identified.** A descriptive User-Agent naming this project and its
  repository goes on every request to both hosts.
- **Bounded.** Concurrency is capped, every request has a timeout, area searches
  have a tile budget and nested OSM relations have a request budget. A retry
  happens once, after a transient failure, never after a 4xx.
- **Cached.** Responses are cached in memory for the session, so a repeated tool
  call in one conversation costs nothing upstream.

Before any public deployment, shared instance, or sustained automated querying:
**contact the Israel Hiking Map maintainers first**, and read OpenStreetMap's
[API usage policy](https://operations.osmfoundation.org/policies/api/). A tool
that makes it easy for a language model to fan out over somebody else's map
tiles is exactly the kind of thing that gets an API closed for everybody.

And the point the whole design turns on: **nothing here is a substitute for a
current map, local knowledge, and carrying enough water.** An answer from this
server is a lead to check, not a plan to follow.
