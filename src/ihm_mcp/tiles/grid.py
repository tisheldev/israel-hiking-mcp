"""Which tiles cover a search area.

Pure geometry: nothing here talks to the network.
"""

from __future__ import annotations

import math

import mercantile

from ihm_mcp.errors import InvalidInputError, SearchAreaTooLargeError
from ihm_mcp.models import BoundingBox, Coordinates

Tile = mercantile.Tile

#: The tileset's own limits, from the map site's source declaration.
MIN_ZOOM = 10
MAX_ZOOM = 14
#: Coarse enough that a 40 km radius stays inside a 100-tile budget. Whether
#: markers survive thinning at this zoom is verified in PR 5, against live data.
DEFAULT_ZOOM = 12

TILE_ROOT = "/vector/data/global_points"

#: A degree of latitude, near enough anywhere. Longitude is scaled per latitude.
KM_PER_DEGREE = 111.32
#: How far past the requested radius a tile may sit and still be fetched.
RADIUS_MARGIN = 1.01
#: Below this, a search is asking about a single street corner.
MIN_SUGGESTED_RADIUS_KM = 0.1


def tile_path(tile: Tile) -> str:
    return f"{TILE_ROOT}/{tile.z}/{tile.x}/{tile.y}.mvt"


def tiles_for_radius(
    center: Coordinates, radius_km: float, *, zoom: int = DEFAULT_ZOOM, max_tiles: int
) -> list[Tile]:
    """The tiles covering the circle of `radius_km` around `center`, as long as
    fetching all of them is a request this server is willing to make."""
    tiles = reachable_tiles(center, radius_km, zoom)
    if len(tiles) > max_tiles:
        # Without a radius that would have worked, a caller has to bisect its
        # way to a search that runs.
        fits = affordable_radius_km(center, radius_km, zoom=zoom, max_tiles=max_tiles)
        raise SearchAreaTooLargeError(
            f"That area needs {len(tiles)} map tiles at zoom {zoom}, and this "
            f"server fetches at most {max_tiles} per call.",
            hint=f"About {fits:g} km fits at zoom {zoom}.",
        )
    return tiles


def reachable_tiles(center: Coordinates, radius_km: float, zoom: int) -> list[Tile]:
    """The tiles overlapping the circle itself, not the box around it: the
    box's corners reach some 40% past the radius and hold nothing a caller
    asked for. At 40 km and zoom 12 that is 98 tiles against 121."""
    box = bounding_box(center, radius_km)
    return [tile for tile in covering_tiles(box, zoom) if reaches(center, radius_km, tile)]


def covering_tiles(box: BoundingBox, zoom: int) -> list[Tile]:
    """Every tile at `zoom` that overlaps `box`, in a stable order — that order
    decides which copy of an edge-straddling feature a search keeps."""
    if not MIN_ZOOM <= zoom <= MAX_ZOOM:
        raise InvalidInputError(
            f"This map's tiles exist for zoom {MIN_ZOOM}–{MAX_ZOOM}; got {zoom}."
        )
    tiles = mercantile.tiles(box.minLng, box.minLat, box.maxLng, box.maxLat, [zoom])
    return sorted(tiles, key=lambda tile: (tile.x, tile.y))


def bounding_box(center: Coordinates, radius_km: float) -> BoundingBox:
    """The smallest lat/lng box containing the circle of `radius_km` around
    `center`. Callers filter to the true distance."""
    if radius_km <= 0:
        raise InvalidInputError(f"`radiusKm` must be greater than 0; got {radius_km:g}.")

    lat_span = radius_km / KM_PER_DEGREE
    # A degree of longitude is shortest at the box edge furthest from the
    # equator; scaling by the centre would leave the far corners outside.
    outermost = min(abs(center.lat) + lat_span, 89.0)
    lng_span = radius_km / (KM_PER_DEGREE * math.cos(math.radians(outermost)))

    return BoundingBox(
        minLat=max(center.lat - lat_span, -90.0),
        minLng=max(center.lng - lng_span, -180.0),
        maxLat=min(center.lat + lat_span, 90.0),
        maxLng=min(center.lng + lng_span, 180.0),
    )


def reaches(center: Coordinates, radius_km: float, tile: Tile) -> bool:
    """Whether any part of `tile` lies within `radius_km` of `center`.

    Carries a margin so it errs towards fetching: a needless tile costs one
    request, while a tile skipped wrongly loses a result and says nothing.
    """
    bounds = mercantile.bounds(tile)
    nearest = Coordinates(
        lat=min(max(center.lat, bounds.south), bounds.north),
        lng=min(max(center.lng, bounds.west), bounds.east),
    )
    return flat_distance_km(center, nearest) <= radius_km * RADIUS_MARGIN


def flat_distance_km(origin: Coordinates, point: Coordinates) -> float:
    """Distance with the earth treated as flat around `origin` — good to well
    under a percent here, and used for nothing but choosing tiles."""
    north = (point.lat - origin.lat) * KM_PER_DEGREE
    east = (
        (point.lng - origin.lng)
        * KM_PER_DEGREE
        * math.cos(math.radians((point.lat + origin.lat) / 2))
    )
    return math.hypot(north, east)


def affordable_radius_km(
    center: Coordinates, radius_km: float, *, zoom: int, max_tiles: int
) -> float:
    """A radius near `radius_km` whose tiles do fit the budget.

    Tile count grows with the square of the radius, so the square root of the
    overshoot gets close — but the count is a step function of the radius, so
    the estimate is checked and walked down until it is true.
    """
    asked_for = len(reachable_tiles(center, radius_km, zoom))
    suggested = radius_km * math.sqrt(max_tiles / asked_for)
    while (
        suggested > MIN_SUGGESTED_RADIUS_KM
        and len(reachable_tiles(center, suggested, zoom)) > max_tiles
    ):
        suggested *= 0.9
    # Rounding down only ever removes tiles, so the answer stays true.
    return max(math.floor(suggested * 10) / 10, MIN_SUGGESTED_RADIUS_KM)
