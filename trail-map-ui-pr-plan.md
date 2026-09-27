# PR 10 — Inline Trail Map with MCP Apps

## Goal

Render the geometry returned by `get_route_details` and
`route_between_points` as an interactive map inside MCP Apps-compatible chat
clients, including ChatGPT/Codex and Claude Desktop.

The map is progressive enhancement. Existing structured and text results stay
complete and usable when a host does not support MCP Apps.

Suggested branch: `feat/trail-map-ui`

## Base branch

Create this PR after PRs 6–9 are present on the target branch. In the current
remote history, the completed MVP is represented by commit `df28ee6`; the
current `main` pointer still ends at PR 5 and does not yet contain the geometry
tools required by this work.

## Scope and design decisions

- Attach one shared map resource to `get_route_details` and
  `route_between_points`.
- Do not add a second tool that fetches or copies geometry. Both existing tools
  already return final, render-ready GeoJSON.
- Do not attach the map to `search_places`, `search_hiking_routes`, or
  `find_pois_along_route`; those results do not contain a complete route line.
- Use the portable MCP Apps protocol only:
  `_meta.ui.resourceUri`, `text/html;profile=mcp-app`, and the standard `ui/*`
  bridge.
- Do not make `window.openai` or another host-specific API part of the required
  path.
- Preserve the current tool schemas, warnings, unknowns, attribution, and text
  fallback.
- Use a low-volume OpenStreetMap raster basemap for this personal,
  non-commercial project. Keep its domain in the resource CSP and show its
  attribution. A public deployment should select a tile provider appropriate
  for its expected traffic.

## Clean architecture

Keep the feature small and split it at real change boundaries:

```text
MCP resource registration
        |
        v
standard MCP Apps bridge -> result normalization -> MapViewModel
                                                   |
                                                   v
                                           Leaflet renderer
```

### Responsibilities

- `src/ihm_mcp/ui.py` owns only the MCP resource URI, resource metadata, and
  loading the packaged HTML.
- Existing tool modules own only tool metadata linking their result to the UI
  resource. They do not gain rendering logic.
- `ui/src/bridge.ts` owns communication with the MCP Apps host.
- `ui/src/normalize.ts` validates raw tool output and converts it into a small
  host-independent `MapViewModel`.
- `ui/src/map.ts` renders a `MapViewModel`; it does not understand MCP messages
  or raw backend result shapes.
- `ui/src/main.ts` is the composition root that connects the bridge,
  normalizer, and renderer.
- `ui/src/styles.css` owns presentation and responsive/RTL behavior.

### SOLID and clean-code constraints

- **Single responsibility:** keep protocol, normalization, and Leaflet code in
  separate modules. No tool function should read HTML or manipulate UI data.
- **Open/closed:** normalize recorded and calculated results through small,
  explicit adapter functions that produce the same `MapViewModel`. A future
  POI overlay should add an adapter/layer without rewriting the bridge.
- **Liskov substitution:** every accepted result adapter must satisfy the same
  `MapViewModel` invariants: valid bounds, independent line parts, display
  metadata, warnings, and attribution.
- **Interface segregation:** define only the narrow types each module uses.
  The renderer receives map lines, markers, and display text rather than the
  entire MCP result envelope.
- **Dependency inversion:** application flow depends on a small bridge
  interface and a map-renderer interface. Concrete MCP Apps and Leaflet
  objects are created in `main.ts`, which also makes the logic testable without
  a real host or browser map.
- Prefer pure functions for type guards, GeoJSON conversion, bounds, and view
  model creation.
- Use descriptive domain names such as `recordedRoute`, `calculatedRoute`,
  `snappedStart`, and `lineParts`; avoid generic `data`, `item`, or `handler`
  names outside tiny local scopes.
- Keep one source of truth for the resource URI and MIME type.
- Do not create a general plugin framework, class hierarchy, or state store for
  this one component.
- Never recompute backend facts such as length, warnings, or safety claims in
  the browser. Render the authoritative values already returned by the tool.
- Treat all upstream strings as untrusted text and insert them with
  `textContent`, never `innerHTML`.

## Implementation tasks

### 1. Add the frontend source and build

Create:

- `ui/package.json`
- `ui/package-lock.json`
- `ui/tsconfig.json`
- `ui/vite.config.ts`
- `ui/index.html`
- `ui/src/types.ts`
- `ui/src/bridge.ts`
- `ui/src/normalize.ts`
- `ui/src/map.ts`
- `ui/src/main.ts`
- `ui/src/styles.css`
- `ui/src/normalize.test.ts`

Use plain TypeScript, Leaflet, `@modelcontextprotocol/ext-apps`, Vite, and
`vite-plugin-singlefile`. Do not add React or another component framework for a
single map.

Build one self-contained HTML document. Commit the generated artifact at:

- `src/ihm_mcp/assets/trail-map-v1.html`

The generated file is not hand-edited; `ui/src` is its source of truth. Keeping
the artifact in the Python package means users still need only Python and `uv`
to run the MCP server. Node is a development dependency for UI changes only.

### 2. Define the UI view model

Create a small discriminated model containing only what the renderer needs:

- `kind`: `recorded` or `calculated`.
- `title` and optional route metrics.
- `lineParts`: an array of independent longitude/latitude lines.
- Start/end marker definitions.
- Requested/snapped endpoints for calculated routes.
- Geometry simplification information.
- `warnings`, `unknowns`, and attribution strings.

Boundary validation must:

- Accept both GeoJSON `LineString` and `MultiLineString`.
- Reject empty lines, non-finite coordinates, and out-of-range positions.
- Preserve each `MultiLineString` member as a separate line; never bridge a gap.
- Distinguish existing `RouteDetails` and `CalculatedRoute` results using
  explicit type guards rather than optional-property chains spread throughout
  the renderer.
- Return a clear fallback state instead of throwing when a host delivers an
  unexpected result.

### 3. Implement map behavior

For recorded routes:

- Draw the recorded route line.
- Mark its first and final recorded positions.
- Fit the viewport to all line parts.
- Show title, length, difficulty, ascent, and descent when present.
- Surface `geometryDetail` when the returned line was simplified.

For calculated routes:

- Use a visually distinct line style.
- Show requested and snapped start/end positions.
- Draw the short requested-to-snapped gaps separately so the displayed route
  does not imply that the router covered them.
- Surface the unconditional calculated-route warning.

For both result types:

- Keep warnings and unknowns available immediately below the map.
- Show all required data and basemap attribution.
- Support Hebrew content with `dir="auto"` and responsive layout.
- Use circle markers or bundled assets so no undeclared Leaflet image domain is
  needed.
- Keep pan, zoom, and fit-to-route controls keyboard accessible.

### 4. Register the MCP Apps resource

Add `src/ihm_mcp/ui.py` with:

- A single `TRAIL_MAP_RESOURCE_URI` constant, for example
  `ui://israel-hiking/trail-map-v1.html`.
- A resource registered through the existing `FastMCP` instance.
- MIME type `text/html;profile=mcp-app`.
- Resource metadata containing `ui.prefersBorder` and a narrow `ui.csp`.
- HTML loading through `importlib.resources`.

Only the selected tile origin should appear in `resourceDomains`. Keep scripts
and styles inside the generated HTML so no CDN script/style permissions are
needed. Version the URI whenever the component contract changes incompatibly.

Update `pyproject.toml` so the HTML asset is included in built wheels and source
distributions.

### 5. Link the existing tools

Update the completed versions of:

- `src/ihm_mcp/tools/routes.py`
- `src/ihm_mcp/tools/routing.py`

Add standard tool metadata:

```python
meta={"ui": {"resourceUri": TRAIL_MAP_RESOURCE_URI}}
```

Keep the existing annotations, input schemas, return types, and error boundary
unchanged. The UI must consume the existing `structuredContent`; the tool
functions should not know whether a host rendered it.

### 6. Preserve graceful degradation

Verify that clients without MCP Apps support still receive the current:

- Structured result.
- JSON text mirror.
- Warnings and unknowns.
- Attribution.

The map must not become required for understanding or safely using the result.

## Tests

### Python protocol and packaging tests

Add `tests/test_ui.py` covering:

- `resources/list` contains exactly one versioned trail-map resource.
- `resources/read` returns the expected URI, MIME type, HTML, and CSP metadata.
- `get_route_details` and `route_between_points` advertise the resource URI.
- Other tools do not advertise a UI resource.
- Existing calls still return structured and text content when the test client
  declares no MCP Apps capability.
- The built wheel contains `trail-map-v1.html`.
- The packaged document has no remote `<script>` or stylesheet dependency.

Keep existing protocol tests focused on the general MCP contract; place
map-specific assertions in `test_ui.py` rather than growing
`test_protocol.py` into a feature test.

### Frontend unit tests

Use Vitest for pure boundary logic:

- Recorded `LineString` normalization.
- Disconnected `MultiLineString` normalization.
- Calculated-route detection.
- Requested and snapped endpoint markers.
- Bounds across several line parts.
- Simplified-geometry messaging.
- Missing optional metadata.
- Empty, invalid, or out-of-range geometry.
- Hebrew text preservation.

Mock the narrow bridge and renderer interfaces. Do not mock Leaflet internals
throughout the tests.

### Manual acceptance

Run the generated app in an MCP Apps reference host, Claude Desktop, and a
ChatGPT/Codex MCP Apps host.

Check:

1. An OSM Haifa Trail result renders all disconnected parts without joining
   gaps.
2. The large Israel National Trail result remains responsive after backend
   simplification.
3. A Users route renders with its recorded metadata.
4. A calculated Carmel route distinguishes requested and snapped endpoints.
5. Hebrew output lays out correctly.
6. Pan, zoom, resize, keyboard navigation, warnings, and attribution work.
7. A client without UI support still receives a complete normal tool result.
8. Browser network activity is limited to the CSP-declared tile origin.

## Documentation and licensing

Update:

- `README.md` with the inline-map behavior, supported host class, frontend
  build command, screenshots, and fallback behavior.
- `LICENSE-NOTICE.md` with Leaflet and MCP Apps frontend dependency notices.
- The tool walkthrough to state that geometry tools render a map in compatible
  hosts.
- Responsible-use wording to make clear that a visually convincing line still
  does not establish current access, safety, waymarking, or passability.

## Acceptance criteria

- The same standard MCP Apps resource renders in ChatGPT/Codex and Claude
  Desktop without host-specific required code.
- Recorded and calculated routes display correctly and are visually distinct.
- `MultiLineString` gaps, snapped routing ends, simplification, safety limits,
  and attribution are not hidden by the visualization.
- Existing tool schemas and non-UI behavior remain backward compatible.
- Runtime installation remains Python/`uv` only.
- Python tests, frontend tests, and the production UI build pass.
- The generated HTML included in the Python package matches the committed UI
  source build.

## Non-goals

- Rendering full geometry for every search result.
- POI overlays.
- Reproducing the complete Mapeak map style or national trail network.
- Route editing, exporting, or offline tiles.
- Adding remote HTTP deployment for Claude.ai.
- Adding UI support to terminal-only hosts such as Claude Code.

A later PR can add POI layers by extending `MapViewModel` and the Leaflet layer
composition without changing the MCP resource, bridge, or existing tool
contracts.
