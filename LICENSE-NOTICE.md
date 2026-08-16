# License and attribution notice

## What this project is

An **unofficial, non-commercial, read-only** prototype MCP server that reads
publicly available data from the Israel Hiking Map project (now branded
[Mapeak](https://mapeak.com)) and from OpenStreetMap.

It is **not affiliated with, endorsed by, or supported by** the Israel Hiking Map
project or its maintainers. It writes nothing upstream and creates no accounts.

## Upstream licenses

| Source | License | Notes |
|---|---|---|
| Israel Hiking Map / Mapeak ([`IsraelHikingMap/Site`](https://github.com/IsraelHikingMap/Site)) | CC BY-NC-SA 3.0 | The upstream `LICENSE.md` states that "all output of the work should be licensed under the same license". |
| OpenStreetMap | ODbL 1.0 | © OpenStreetMap contributors, <https://www.openstreetmap.org/copyright> |

Because of the **NC (non-commercial)** and **SA (share-alike)** terms, output
produced by this server carries the same conditions: it may not be used
commercially, and derivative works must be shared under the same license.

## Frontend dependencies

The inline trail map (`ui/src`, built into
`src/ihm_mcp/assets/trail-map-v1.html`) bundles third-party code. Their licenses
are permissive and compatible with distributing the built document; each keeps
its own notice in the bundle.

| Component | License | Notes |
|---|---|---|
| [Leaflet](https://leafletjs.com) | BSD-2-Clause | © Volodymyr Agafonkin, © CloudMade. Map rendering. |
| [MCP Apps SDK](https://github.com/modelcontextprotocol/ext-apps) (`@modelcontextprotocol/ext-apps`) | MIT | Host bridge for the MCP Apps protocol. |
| [Vite](https://vite.dev), [vite-plugin-singlefile](https://github.com/richardtallent/vite-plugin-singlefile), [Vitest](https://vitest.dev), [TypeScript](https://www.typescriptlang.org) | MIT / Apache-2.0 | Build and test tooling. Not shipped in the built document. |

**Basemap tiles** are served by the OpenStreetMap Foundation from
`tile.openstreetmap.org` and are **not** part of this repository. They are
© OpenStreetMap contributors, ODbL, and their use is governed by the
[tile usage policy](https://operations.osmfoundation.org/policies/tiles/), which
covers low-volume personal use only. The document displays their attribution.
Any public deployment must select a tile provider appropriate for its traffic.

## Attribution requirement

Every tool response from this server includes an attribution string. It must be
preserved when the output is displayed or redistributed:

> Data from Israel Hiking Map / Mapeak (https://mapeak.com), licensed
> CC BY-NC-SA 3.0, and from OpenStreetMap contributors
> (https://www.openstreetmap.org/copyright), licensed ODbL. This is an
> unofficial, non-commercial, read-only prototype and is not affiliated with or
> endorsed by the Israel Hiking Map project.

## Responsible use

This server is built for low-volume personal and portfolio use. Before any
public deployment or sustained automated querying, the Israel Hiking Map
maintainers should be contacted, and OpenStreetMap's
[API usage policy](https://operations.osmfoundation.org/policies/api/) must be
respected — including a descriptive User-Agent, aggressive caching, and bounded
request rates.

A rendered map does not change any of this. A route drawn as a clean line over a
real basemap looks authoritative in a way the same coordinates in JSON do not,
but it establishes nothing further about access, safety, waymarking or
passability — and for a calculated path, nothing there was ever walked.

## This repository's own code

The Python source in this repository, and the frontend source in `ui/src`, are
the author's original work. Both are distributed under the same CC BY-NC-SA 3.0
terms as the upstream data they are designed to read, to avoid any ambiguity
about the combined work. Bundled third-party frontend code keeps its own license,
listed under [Frontend dependencies](#frontend-dependencies).
