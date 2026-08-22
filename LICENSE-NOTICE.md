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

## This repository's own code

The Python source in this repository is the author's original work. It is
distributed under the same CC BY-NC-SA 3.0 terms as the upstream data it is
designed to read, to avoid any ambiguity about the combined work. The full
legal text is in [LICENSE](LICENSE); this file is the plain-language companion
to it, and covers the upstream data as well as the code.
