# Israel Hiking MCP — Implementation Plan

Plan for the MVP described in `israel_hiking_mcp_mvp_prd.md`. Built as a new standalone repo
(`D:\Projects\IHM\israel-hiking-mcp`), no changes to the upstream `IsraelHikingMap/Site` repo.
Work is split into 9 small PRs, each sized to teach one concept.

Status: **awaiting approval — no code written yet.**

---

## 1. Verified integration facts (repo inspection + live requests, 2026-07-11)

All verified against the local `Site` repo and minimal read-only requests to production.

| Fact | Value | Evidence |
|---|---|---|
| Base URL | `https://mapeak.com` (project rebranded from israelhiking.osm.org.il to "Mapeak") | `environment.prod.ts` |
| Place search | `GET /api/search/{term}?language={he\|en}` → array of `{id, source, title, displayName, icon, iconColor, location{lat,lng,alt}, hasExtraData}` | `SearchController.cs`, live call OK |
| Route/POI tiles | `GET /vector/data/global_points/{z}/{x}/{y}.mvt`, **zoom 10–14**, two source layers: **`global_points`** and **`external`** | `public-pois.component.html` (minzoom 10, maxzoom 14), live z14 call → 200 `application/x-protobuf` |
| Tile feature identity | `properties.identifier` if present, else derived from MVT feature id: last digit `1`=node, `2`=way, else relation; `osmId = floor(id/10)` | `poi.service.ts` `osmTileFeatureToPoiIdentifier` |
| Route marker properties | `poiCategory` (`Hiking`/`Bicycle`/`4x4`/`Unknown`), `poiDifficulty`, `poiLength` (meters), `poiUserId`, `poiSource`, `identifier`, `poiGeolocation` (**may arrive as a JSON string** — must parse), `name`/`name:he`/`name:en`, `description*`, `image` | `poi.service.ts` `convertFeatureToPoi`, `getPublicRoutes` |
| User-shared route details | `GET /api/urls/{id}` → `ShareUrl` with `dataContainer.routes[].segments[].latlngs[]` (`{lat,lng,alt,timestamp?}`); also `GET /api/urls/{id}/timestamp`, thumbnail at `/api/urls/{id}/thumbnail` | `share-urls.service.ts`, `ShareUrl.cs`, `DataContainerPoco.cs` |
| OSM route details | Frontend calls the **public OSM API directly**: `https://api.openstreetmap.org/api/0.6/{type}/{id}[/full].json`, recursively fetching nested relations, then converts with `osm2geojson-lite` and merges line segments | `overpass-turbo.service.ts` `getFeature` |
| Overpass (secondary) | `POST https://mapeak.com/api/interpreter` — used only for "long way" stitching and place polygons; **not needed for MVP** | `overpass-turbo.service.ts` |
| Routing | `GET /api/routing?from={lat},{lng}&to={lat},{lng}&type={Hike\|Bike\|4WD\|None}` → GeoJSON FeatureCollection, coordinates include elevation | `RoutingController.cs`, live call OK |
| IHM POI link | `https://mapeak.com/poi/{source}/{identifier}?language={lang}` (e.g. `/poi/OSM/relation_123456`) | `hash.service.ts` |
| IHM share link | `https://mapeak.com/share/{id}` | `share-urls.service.ts` |
| Water tag→category mapping | `natural=spring\|waterhole\|water`; `water=reservoir\|pond\|lake\|stream_pool`; `man_made=water_well\|cistern`; `waterway=waterfall`; any `waterway` → category `Water` | `osm-tags.service.ts` |
| License | CC BY-NC-SA 3.0, "all output of the work should be licensed under the same license" | `LICENSE.md` |
| MCP Python SDK | Stable: **`mcp` 1.28.1** (v1.x recommended for production; `2.0.0a1` is alpha — do not use). Requires Python ≥3.10 | PyPI, 2026-07-11 |

### Discrepancies vs the PRD (adjustments, none scope-breaking)

1. **Search is global, not Israel-only.** A live search for "Haifa" returns matches in Nablus, France, and the US before Haifa, Israel. `search_places` must re-rank/optionally filter results by an Israel bounding box (about 29.3–33.4 N, 34.2–35.9 E) and expose that behavior in its schema. The `lat/lng/zoom/prefix` params the frontend sends are ignored by the current `SearchController` — don't rely on them.
2. **OSM details come from `api.openstreetmap.org`, not an IHM endpoint.** That is "the same source strategy the frontend uses," as the PRD requires, but it means the MCP talks to a second external host (with its own attribution and usage policy).
3. **Tile budget math constrains the search radius zoom.** At z14 a 40 km radius needs ~1,300 tiles. Plan: fetch at **z12 by default** (1–98 tiles for 1–40 km, budget 100), and verify during PR 5 that the tileset contains all route markers at z12; if low zooms are thinned, cap `radiusKm` lower or raise budget deliberately. *(Measured in PR 4: the bounding box of a 40 km circle costs 121 tiles at z12, over budget — so `tiles_for_radius` covers the circle itself, 98 tiles, and drops the corner tiles that cannot hold an in-radius result.)*
4. **Server-side `ShareUrl.cs` lacks `type/difficulty/length/gain/loss/start`** but the client's OpenAPI-generated model has them. Treat all of them as optional in our Pydantic model and never assume presence.
5. **The C# API has no swagger doc for tiles** — the MVT contract is only what the frontend consumes. Treat unknown layer/property changes as `upstream_schema_changed`.

---

## 2. Tech stack

- Python **3.11+**, `uv` for packaging/venv/locking.
- `mcp >=1.28,<2` (official SDK, `FastMCP` server API, stdio transport).
- `httpx` (async client, timeouts, connection limits), `pydantic` v2, `mercantile` (lat/lng↔tile), `mapbox-vector-tile` (MVT decode), `shapely` (buffers/distances), `osm2geojson` (OSM JSON → GeoJSON; if it disappoints, write a focused converter — the frontend's needs are narrow).
- Distance strategy: project coordinates with a simple **local equirectangular scaling** around the route centroid (cos-latitude correction) before Shapely operations. At Israel's extent and ≤2 km buffers the error is well under 1%; documented in `spatial.py`. No pyproj/GDAL/GeoPandas.
- Tests: `pytest`, `pytest-asyncio`, `respx` (httpx mocking).

## 3. Project layout

As in the PRD §7 (`src/ihm_mcp/` with `server.py`, `config.py`, `models.py`, `errors.py`, `ihm_client.py`, `tiles.py`, `route_sources.py`, `spatial.py`, `tools/`, `tests/`). Modules are introduced PR by PR — the skeleton PR only creates what it needs.

---

## 4. PR-by-PR breakdown

Each PR: small enough to review in one sitting, lands with its own tests, and has an explicit "what you learn" focus. Suggested branch names in parentheses.

### PR 1 — Repo + MCP server skeleton (`feat/server-skeleton`)
**What you learn:** what MCP actually is on the wire — JSON-RPC over stdio, initialize handshake, `tools/list`/`tools/call`, and why stdout belongs to the protocol (all logging goes to stderr).

- `uv init`, `pyproject.toml` pinning `mcp>=1.28,<2`, README stub, `LICENSE-NOTICE.md` (CC BY-NC-SA + OSM attribution, "unofficial, non-commercial, read-only prototype").
- `server.py` with `FastMCP("israel-hiking")` and one throwaway `ping` tool returning server version + attribution (deleted in PR 4 once real tools exist).
- `logging` configured to stderr only.
- Tests: server starts over stdio, `tools/list` shows `ping`, calling it returns structured content, invalid args are rejected by the schema boundary, clean shutdown. (Use the SDK's in-memory/client test helpers.)
- Manual check: MCP Inspector connects and calls `ping`.

**Done when:** `uv run israel-hiking-mcp` serves stdio and Inspector can call the tool.

### PR 2 — Config, HTTP client, typed errors (`feat/http-foundation`)
**What you learn:** disciplined outbound IO — timeouts everywhere, bounded concurrency, retry only on transient failures, responsible User-Agent, and error taxonomy design.

- `config.py`: env-driven settings (`IHM_BASE_URL`, `IHM_REQUEST_TIMEOUT_SECONDS=10`, `IHM_USER_AGENT`, `IHM_CACHE_TTL_SECONDS=300`, `IHM_MAX_CONCURRENT_REQUESTS=4`, `IHM_MAX_TILES_PER_TOOL_CALL=100`) via pydantic-settings or plain dataclass.
- `errors.py`: the PRD's typed errors (`invalid_input`, `place_not_found`, `route_not_found`, `unsupported_source`, `search_area_too_large`, `geometry_too_large`, `upstream_timeout`, `upstream_unavailable`, `upstream_schema_changed`, `rate_limited`) as an exception hierarchy that serializes to concise tool errors — never raw HTML/stack traces/paths.
- `ihm_client.py`: shared `httpx.AsyncClient` (semaphore-capped), `get_json`/`get_bytes`, in-memory TTL cache keyed by URL, bounded retry (1 retry on timeout/5xx only), HTTP status → typed-error mapping (404, 429, 5xx, invalid JSON).
- Tests with respx: timeout, 404, 429, 5xx, invalid-body mapping; cache hit behavior; retry-once behavior.

**Done when:** all upstream failure modes surface as the typed errors and nothing else.

### PR 3 — Models + `search_places` (`feat/search-places`)
**What you learn:** designing agent-facing tool contracts — strict Pydantic input/output models, tool descriptions that state what the tool can't establish, provenance in every result.

- `models.py`: `Coordinates`, `PlaceResult`, `Attribution`, `RouteRef`/`PoiRef` (`{source, identifier}` — identity is never a bare id).
- `tools/places.py`: validate/trim/bound query (3–100 chars — upstream returns nothing under 3), call `/api/search/{term}`, normalize, build `ihmUrl` per source, **rank Israel-bbox results first** with an `israelOnly: bool = true` input (the global-results discrepancy above), return empty list on no match, never auto-select.
- Hebrew + English test cases (URL-encoding of Hebrew terms), fixtures from the real response shape captured today.
- MCP contract test updated: `search_places` listed with the expected schema.

**Done when:** Inspector call for "Haifa" (live) returns Haifa, Israel first with a working mapeak.com link — and the automated suite passes offline.

### PR 4 — Tile engine (`feat/tile-engine`)
**What you learn:** the slippy-map tile scheme (z/x/y, Web Mercator), the MVT format (layers, integer geometries, tile-local coordinates → lng/lat), and budget-bounded fan-out.

Pure library code, no new tool yet — this is deliberately a separate PR because it's the densest learning chunk.

- `tiles.py`:
  - `tiles_for_radius(center, radius_km, zoom)` via mercantile, returning a bounded tile set; raise `search_area_too_large` (with the actual count and the limit) past `IHM_MAX_TILES_PER_TOOL_CALL`.
  - async fetch of `/vector/data/global_points/{z}/{x}/{y}.mvt` through `ihm_client` (cached, concurrency-capped).
  - decode **both** `global_points` and `external` layers; convert tile-local coords to lng/lat; extract properties + MVT feature id.
  - port `osmTileFeatureToPoiIdentifier` (id-digit trick) and the `poiGeolocation`-string-parse quirk.
  - dedupe across overlapping tiles by `(source, identifier)`.
- Tests: tile math golden cases; budget rejection; decoding **synthetic MVT fixtures** built in-test with `mapbox-vector-tile`'s encoder (no licensed data committed); dedupe; malformed-tile → `upstream_schema_changed`/`upstream_unavailable`.

**Done when:** given fixture tiles, the engine yields normalized, deduped point features with stable refs.

### PR 5 — `search_hiking_routes` (`feat/route-search`)
**What you learn:** deterministic filtering/sorting as a product guarantee (same inputs → same output → trustable evals), and mirroring upstream semantics (`poiLength` meters, difficulty enum, category names).

- `models.py`: `RouteSummary`, `BoundingSearch`, warnings list.
- `tools/routes.py`: validate (`radiusKm` 1–40, `limit` 1–20, lengths, difficulty enum `Easy/Moderate/Hard`), run tile engine at z12, keep `poiCategory == "Hiking"` (schema documents that only Hiking is returned in MVP), filter by length range / difficulty / start-distance ≤ radius (haversine), sort by (distance from center, constraint fit, ref) — stable and documented.
- `distanceFromSearchCenterKm`, `lengthKm` from `poiLength/1000`, `ihmUrl`, `dataSource`, attribution, `searchedArea` echo.
- **Verify live at this point that z12 tiles carry all route markers** (compare a z12 tile against its four z13/16 z14 children for a known area). If thinned, document and switch default zoom / lower max radius — this is the plan's flagged risk item.
- Tests: filter/sort determinism, empty results, radius→budget errors, fixture-driven end-to-end of the tool.

**Done when:** Inspector: routes near Haifa, 4–12 km, Moderate → plausible real routes, deterministically ordered.

### PR 6 — `get_route_details`, part 1: Users source (`feat/route-details-users`)
**What you learn:** adapter pattern for heterogeneous sources; converting a bespoke JSON shape (segments/latlngs) into GeoJSON; geometry simplification with explicit metadata.

- `route_sources.py`: `RouteSourceAdapter` protocol — `resolve(ref, language) -> ResolvedRoute` (geometry + metadata). Registry keyed by source; unknown source → `unsupported_source` (no guessed URLs).
- `UsersAdapter`: `GET /api/urls/{id}`, flatten `routes[].segments[].latlngs` → LineString/MultiLineString (port `convertShareUrlToPoi` logic), pull title/description/difficulty/length/gain when present (all optional — see discrepancy 4), `ihmUrl` = share link.
- `spatial.py`: `simplify_geometry(geom, tolerance)` (Shapely Douglas-Peucker) with a coordinate-count cap → `geometry_too_large` if still over; response carries `simplified: bool` + `toleranceM`.
- `tools/routes.py`: `get_route_details` tool with fixed `unknowns` (closure status, water availability) and warnings.
- Tests: dataContainer→GeoJSON fixture conversion, multi-route shares, simplification metadata, 404 → `route_not_found`.

**Done when:** a real share id renders correct geometry and metadata via Inspector.

### PR 7 — `get_route_details`, part 2: OSM source (`feat/route-details-osm`)
**What you learn:** the OSM data model (nodes/ways/relations, `/full` expansion, nested relations) — the heart of how hiking routes actually exist in OSM.

- `OsmAdapter`: parse `relation_123456`-style identifiers; fetch `https://api.openstreetmap.org/api/0.6/{type}/{id}[/full].json`; recursive nested-relation fetch with visited-set (port `handleNestedRelations`, cap depth/requests); convert via `osm2geojson`; merge MultiLineString parts into one line where they chain (port the frontend's `processFeature`/`mergeLines` semantics).
- Separate httpx client config for openstreetmap.org (same User-Agent policy); OSM attribution appended.
- Tests: node/way/relation fixtures, nested-relation recursion + loop guard, unsupported source (`Nakeb`, `iNature`, `Wikidata` → typed error), identifier parse failures.

**Done when:** an OSM hiking relation from PR 5's search results returns a usable line geometry end-to-end.

### PR 8 — `find_pois_along_route` (`feat/pois-along-route`)
**What you learn:** buffer/distance geospatial computation done honestly (local projection trick + its error bounds) and the evidence/safety model — the "mapped fact ≠ current truth" discipline that makes this portfolio-worthy.

- `spatial.py`: local-equirectangular transform, `buffer envelope`, `nearest distance point→line` in meters.
- `tools/pois.py`: resolve geometry via the PR 6/7 adapters → tiles intersecting envelope+buffer (budgeted) → decode → **category mapping ported from `osm-tags.service.ts`** (Water subtypes: Spring, Waterhole, Well, Cistern, Waterfall, Reservoir/Pond/Lake/StreamPool, Tap/DrinkingWater from `amenity=drinking_water`/`man_made=water_tap`) → filter by requested categories → exact distance → dedupe/sort.
- Output keeps `subtype` + raw `mappedTags`; `evidence: {kind: "mapped_feature", observedAt: null, freshnessKnown: false}`; the mandatory water warning on every water POI.
- Bounds: `bufferMeters` 25–2000, `limit` 1–50, tile + geometry caps.
- Tests: synthetic route + POI fixtures with hand-computed distances (±tolerance), category mapping table, warning always present, buffer bounds.

**Done when:** water POIs along a real Haifa-area route come back distance-ranked with correct subtypes.

### PR 9 — `route_between_points` + demo readiness (`feat/demo-ready`)
**What you learn:** shipping — packaging an MCP server so a host can actually run it, and writing the honest README (limits, licensing, responsible use).

- `tools/routing.py`: wrap `GET /api/routing` (`activity` → `Hike/Bike/4WD/None`), return GeoJSON + attribution + the mandatory "calculated path, not a verified itinerary" warning on every response.
- README: setup (`uv`), Claude Desktop/Claude Code + generic MCP client config JSON, MCP Inspector instructions, tool catalog, known limitations, licensing/attribution section, request-limit/caching documentation.
- Opt-in live integration test group (`-m live`, skipped by default; suite passes fully offline).
- Run the full Haifa manual acceptance scenario from a real host; fix what it surfaces; record the transcript summary in the README.

**Done when:** every PRD §15 acceptance criterion is checked off.

---

## 5. Risks and open items

1. **z12 tile completeness** (flagged in PR 5) — the one unknown that can change scope; fallback is smaller max radius or a higher tile budget at z13.
2. **Upstream churn** — the repo is mid-rebrand (mapeak.com) and the API has no formal contract for tiles; mitigated by `upstream_schema_changed` fail-safe and fixtures pinned to today's shapes.
3. **OSM API courtesy** — details go to api.openstreetmap.org; keep the responsible User-Agent, cache aggressively, and note OSM's usage policy in the README.
4. **License** — CC BY-NC-SA 3.0 with "output under same license": the MCP repo ships as unofficial/non-commercial, `LICENSE-NOTICE.md` from PR 1, and the README commits to contacting IHM maintainers before any public deployment or sustained automated use.
5. **`external` layer contents unknown** — decoded from PR 4 onward; if it only carries non-OSM sources (Nakeb/iNature), those appear in search results but return `unsupported_source` from details, which is correct MVP behavior.

## 6. Questions for IHM maintainers (nice-to-have, not blockers)

- Is `global_points` feature completeness guaranteed at z10–12, or are points thinned at lower zooms?
- Any rate limits/courtesy expectations on `/api/search`, `/api/urls`, `/api/routing`, and the vector tiles for a low-volume personal tool?
- Is the `/poi/{source}/{id}` URL format stable through the Mapeak rebrand?
