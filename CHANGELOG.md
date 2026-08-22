# Changelog

Notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A note on what counts as a breaking change here: the tool names, their
arguments, the shape of what they return, and the error codes in the
[README](README.md#error-codes) are this project's public surface. Upstream data
moving — a trail remapped, a spring removed — is not a change to this project
and is not recorded below.

## [Unreleased]

Nothing yet.

## [0.1.0] — 2026-08-15

First release. Five read-only tools over Israel Hiking Map / Mapeak and
OpenStreetMap data, built across nine reviewed pull requests.

### Added

- **`search_places`** — resolve a name to coordinates, ranked, with the matches
  outside Israel reported rather than silently dropped.
- **`search_hiking_routes`** — hiking routes within a radius, filterable by
  length, read from the map's own vector tiles.
- **`get_route_details`** — full geometry, length and climb for a route from the
  `OSM` or `Users` source. `Nakeb`, `iNature` and `Wikidata` are refused by name
  with `unsupported_source` rather than guessed at.
- **`find_pois_along_route`** — points of interest within a buffer of a route,
  by category, with a standing caution on every mapped water feature.
- **`route_between_points`** — a routed path between two points for `Hiking`,
  `Bicycle` or `4x4`, with both ends reporting how far they were snapped.
- A tile engine (grid, decode, reader) covering zoom 10–14 of
  `global_points`, with a tile budget so an area search cannot run away.
- An `attribution` object and an `unknowns` list on every response, and typed
  errors under ten stable codes.
- 400 offline tests, plus an opt-in `-m live` group that asserts contract
  against the real hosts.

### Notes

- `activity: "None"` is deliberately not offered. Upstream defines it, but a
  live request answers HTTP 500, and the map site interpolates that straight
  line client-side. A straight line is not worth a request to a volunteer-run
  service, and calling one a route would be misleading.
- Every measurement in the README carries the date it was taken. Two had
  already drifted upstream within a day of being written down.

[Unreleased]: https://github.com/tisheldev/israel-hiking-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tisheldev/israel-hiking-mcp/releases/tag/v0.1.0
