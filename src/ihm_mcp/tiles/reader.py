"""Fetching the tiles of an area and collecting the features in them."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Protocol

import anyio

from ihm_mcp.errors import IhmError, UpstreamNotFound
from ihm_mcp.models import Coordinates
from ihm_mcp.tiles.decode import TilePoint, decode_tile
from ihm_mcp.tiles.grid import DEFAULT_ZOOM, Tile, tile_path, tiles_for_radius

logger = logging.getLogger(__name__)

MVT_CONTENT_TYPE = "application/x-protobuf"


class TileFetcher(Protocol):
    """All this module asks of the upstream client: bytes for a path."""

    async def get_bytes(self, path: str, *, accept: str | None = None) -> bytes: ...


async def points_in_radius(
    client: TileFetcher,
    center: Coordinates,
    radius_km: float,
    *,
    max_tiles: int,
    zoom: int = DEFAULT_ZOOM,
) -> list[TilePoint]:
    """Every distinct marker in the tiles covering `radius_km` around `center`.

    The tiles overshoot the circle; filtering to the true radius belongs to the
    tool, which knows what the distance means for its own results.
    """
    tiles = tiles_for_radius(center, radius_km, zoom=zoom, max_tiles=max_tiles)
    return await points_in_tiles(client, tiles)


async def points_in_tiles(client: TileFetcher, tiles: Sequence[Tile]) -> list[TilePoint]:
    """Fetch and decode `tiles` concurrently, then dedupe across them.

    One failed tile fails the whole call. Returning what the others held would
    be a result with a hole in it that nothing in the response could describe,
    and a model would read it as "nothing there".
    """
    decoded: dict[Tile, list[TilePoint]] = {}
    failures: dict[Tile, IhmError] = {}

    async def collect(tile: Tile) -> None:
        try:
            decoded[tile] = decode_tile(tile, await fetch_tile(client, tile))
        except IhmError as failure:
            failures[tile] = failure
            # The call is already lost, and the courteous thing is to stop
            # asking a host that just failed to answer.
            group.cancel_scope.cancel()

    # Asks for all of them at once; the client's semaphore caps what is in flight.
    async with anyio.create_task_group() as group:
        for tile in tiles:
            group.start_soon(collect, tile)

    # Report by tile order, not by whichever task lost the race, so the same
    # broken area produces the same error twice running.
    for tile in tiles:
        if tile in failures:
            raise failures[tile]

    return dedupe(point for tile in tiles for point in decoded[tile])


async def fetch_tile(client: TileFetcher, tile: Tile) -> bytes:
    """One tile's bytes; empty where the tileset has nothing to say.

    Tile servers do not cut tiles for empty ground, so a 404 means the area
    holds no features rather than that the endpoint is wrong — treating it as a
    failure would break every search whose box touches the sea.
    """
    try:
        return await client.get_bytes(tile_path(tile), accept=MVT_CONTENT_TYPE)
    except UpstreamNotFound:
        logger.debug("no tile at %s", tile_path(tile))
        return b""


def dedupe(points: Iterable[TilePoint]) -> list[TilePoint]:
    """First occurrence of each feature wins: a marker near a tile edge is
    written into both neighbouring tiles, so adjacent tiles both carry it.

    Identity is the (source, identifier) pair — the same key upstream forms as
    `poiId`.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[TilePoint] = []
    for point in points:
        key = (point.ref.source, point.ref.identifier)
        if key not in seen:
            seen.add(key)
            unique.append(point)
    return unique
