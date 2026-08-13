"""Reading map features out of vector tiles.

The markers this server searches — routes and points of interest — have no query
API. They exist only as the pre-rendered tiles the map site itself draws:
`GET /vector/data/global_points/{z}/{x}/{y}.mvt`, zoom 10 to 14, carrying two
source layers, `global_points` and `external`. So "what is near this point"
becomes: work out which tiles cover the area, fetch them, and decode the Mapbox
Vector Tile format back into coordinates.

Three consequences of that format shape everything below.

* **Positions are tile-local integers.** A feature sits on a 0–`extent` grid
  anchored to its own tile, so decoding needs the tile's Web Mercator bounds to
  recover lng/lat — and the grid quantises, to roughly 2.4 m at zoom 12.
* **Properties are scalars.** MVT cannot carry an object, so `poiGeolocation` —
  the position upstream computed for a feature, more trustworthy than the drawn
  marker — arrives as a JSON string that has to be parsed.
* **Identity is packed into the feature id.** Most features carry no
  `identifier` property; their OSM type and id are encoded in the MVT id by a
  convention documented nowhere except the site's own `poi.service.ts`.

The fan-out is bounded. Tile count grows with the square of the radius, and
this server will not spend hundreds of requests on a volunteer-run map without
telling the caller what it is about to cost.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable, Iterable, Iterator, Sequence
from itertools import islice
from typing import Any

import anyio
import mapbox_vector_tile
import mercantile
from pydantic import ValidationError

from ihm_mcp.errors import (
    IhmError,
    InvalidInputError,
    SearchAreaTooLargeError,
    UpstreamNotFound,
    UpstreamSchemaChangedError,
)
from ihm_mcp.ihm_client import UpstreamClient
from ihm_mcp.models import BoundingBox, Coordinates, FeatureRef, Model

logger = logging.getLogger(__name__)

Tile = mercantile.Tile

#: The tileset's own limits, from the map site's source declaration.
MIN_ZOOM = 10
MAX_ZOOM = 14
#: Coarse enough that a 40 km radius stays inside a 100-tile budget. Whether
#: markers survive thinning at this zoom is verified in PR 5, against live data.
DEFAULT_ZOOM = 12

#: Both source layers in the tileset. `external` holds the non-OSM datasets
#: (Nakeb, iNature); its features are searchable here even where later tools
#: cannot resolve their details.
LAYERS = ("global_points", "external")

TILE_ROOT = "/vector/data/global_points"
MVT_CONTENT_TYPE = "application/x-protobuf"

#: What upstream assumes when a feature does not name its dataset.
DEFAULT_SOURCE = "OSM"

#: A degree of latitude, near enough anywhere. Longitude is scaled per latitude.
KM_PER_DEGREE = 111.32

#: How far past the requested radius a tile may sit and still be fetched. Covers
#: the flat-earth approximation in `reaches` several times over.
RADIUS_MARGIN = 1.01

#: The smallest radius worth suggesting after an over-budget search. Below this
#: a search is asking about a single street corner.
MIN_SUGGESTED_RADIUS_KM = 0.1

#: Geometry types whose position is the middle of their extent rather than
#: their first vertex, matching `getGeolocation` in the site's `poi.service.ts`.
AREA_TYPES = ("Polygon", "MultiPolygon")

Position = tuple[float, float]
Projector = Callable[[float, float], Position]


class TilePoint(Model):
    """One marker decoded from a tile, before any tool decides what it means.

    Internal to this server: tools translate these into their own result models.
    `properties` is upstream's own property bag, passed through unedited — a
    tool reads `poiCategory`, `poiLength`, `name:he` and friends out of it.
    """

    ref: FeatureRef
    coordinates: Coordinates
    layer: str
    properties: dict[str, Any]


# --- which tiles cover an area -----------------------------------------------


def bounding_box(center: Coordinates, radius_km: float) -> BoundingBox:
    """The smallest lat/lng box containing the circle of `radius_km` around
    `center`.

    A box, not a disc: tiles are rectangles, so the corners reach about 40%
    further than the radius asked for. Callers filter to the true distance.
    """
    if radius_km <= 0:
        raise InvalidInputError(f"`radiusKm` must be greater than 0; got {radius_km:g}.")

    lat_span = radius_km / KM_PER_DEGREE
    # Meridians converge towards the poles, so a degree of longitude is shortest
    # at the box edge furthest from the equator. Scale by that edge — scaling by
    # the centre would leave the far corners outside the box.
    outermost = min(abs(center.lat) + lat_span, 89.0)
    lng_span = radius_km / (KM_PER_DEGREE * math.cos(math.radians(outermost)))

    return BoundingBox(
        minLat=max(center.lat - lat_span, -90.0),
        minLng=max(center.lng - lng_span, -180.0),
        maxLat=min(center.lat + lat_span, 90.0),
        maxLng=min(center.lng + lng_span, 180.0),
    )


def covering_tiles(box: BoundingBox, zoom: int) -> list[Tile]:
    """Every tile at `zoom` that overlaps `box`.

    Ordered by (x, y) so that a search over the same area answers in the same
    order every time — the tile sequence decides which duplicate of an
    edge-straddling feature is the one kept.
    """
    if not MIN_ZOOM <= zoom <= MAX_ZOOM:
        raise InvalidInputError(
            f"This map's tiles exist for zoom {MIN_ZOOM}–{MAX_ZOOM}; got {zoom}."
        )
    return sorted(
        mercantile.tiles(box.minLng, box.minLat, box.maxLng, box.maxLat, [zoom]),
        key=lambda tile: (tile.x, tile.y),
    )


def reachable_tiles(center: Coordinates, radius_km: float, zoom: int) -> list[Tile]:
    """The tiles covering the circle of `radius_km` around `center`.

    The circle, not the box around it. The corners of that box reach about 40%
    past the radius and hold nothing a caller asked for; at 40 km and zoom 12,
    dropping them is the difference between 121 tiles and a request this server
    is willing to make.
    """
    box = bounding_box(center, radius_km)
    return [tile for tile in covering_tiles(box, zoom) if reaches(center, radius_km, tile)]


def tiles_for_radius(
    center: Coordinates, radius_km: float, *, zoom: int = DEFAULT_ZOOM, max_tiles: int
) -> list[Tile]:
    """The tiles for that circle, if fetching them all is a request this server
    will make.

    The count and the limit both go into the message, with a radius that would
    have worked: a caller told only "too large" has to bisect its way to a
    search that runs.
    """
    tiles = reachable_tiles(center, radius_km, zoom)
    if len(tiles) > max_tiles:
        fits = affordable_radius_km(center, radius_km, zoom=zoom, max_tiles=max_tiles)
        raise SearchAreaTooLargeError(
            f"That area needs {len(tiles)} map tiles at zoom {zoom}, and this "
            f"server fetches at most {max_tiles} per call.",
            hint=f"About {fits:g} km fits at zoom {zoom}.",
        )
    return tiles


def reaches(center: Coordinates, radius_km: float, tile: Tile) -> bool:
    """Whether any part of `tile` lies within `radius_km` of `center`.

    Measured to the nearest point of the tile, on a flat earth — good to well
    under a percent at these distances, and carrying a margin so it errs
    towards fetching. A tile fetched needlessly costs one request; a tile
    skipped wrongly loses a real result and says nothing about it. Ranking
    results by distance is a later tool's job, with a real haversine.
    """
    bounds = mercantile.bounds(tile)
    nearest = Coordinates(
        lat=min(max(center.lat, bounds.south), bounds.north),
        lng=min(max(center.lng, bounds.west), bounds.east),
    )
    return flat_distance_km(center, nearest) <= radius_km * RADIUS_MARGIN


def flat_distance_km(origin: Coordinates, point: Coordinates) -> float:
    """Distance with the earth treated as flat around `origin` — near enough
    for deciding which tiles to ask for, and nothing else."""
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
    overshoot gets close — but count is a step function of the radius, and near
    the limit "close" lands on the wrong side of it often enough. So the
    estimate is checked, and walked down until it is true. A suggestion the
    caller cannot use either is worse than none.
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


def tile_path(tile: Tile) -> str:
    return f"{TILE_ROOT}/{tile.z}/{tile.x}/{tile.y}.mvt"


# --- fetching and decoding ---------------------------------------------------


async def points_in_radius(
    client: UpstreamClient,
    center: Coordinates,
    radius_km: float,
    *,
    zoom: int = DEFAULT_ZOOM,
) -> list[TilePoint]:
    """Every distinct marker in the tiles covering `radius_km` around `center`.

    The tiles overshoot the circle; filtering to the true radius belongs to the
    tool, which knows what the distance means for its own results.
    """
    tiles = tiles_for_radius(
        center, radius_km, zoom=zoom, max_tiles=client.settings.max_tiles_per_tool_call
    )
    return await points_in_tiles(client, tiles)


async def points_in_tiles(client: UpstreamClient, tiles: Sequence[Tile]) -> list[TilePoint]:
    """Fetch and decode tiles concurrently, then dedupe across them.

    Concurrency is bounded by the shared client's semaphore, so this asks for
    all of them at once and lets the client decide how many are in flight.

    One failed tile fails the whole call. Returning what the other tiles held
    would be a result with a hole in it that nothing in the response could
    describe — and a model would read it as "nothing there".
    """
    decoded: dict[Tile, list[TilePoint]] = {}
    failures: dict[Tile, IhmError] = {}

    async def collect(tile: Tile) -> None:
        try:
            decoded[tile] = decode_tile(tile, await fetch_tile(client, tile))
        except IhmError as failure:
            failures[tile] = failure
            # Stop the rest: the call is already lost, and the courteous thing
            # is to stop asking a host that just failed to answer.
            group.cancel_scope.cancel()

    async with anyio.create_task_group() as group:
        for tile in tiles:
            group.start_soon(collect, tile)

    for tile in tiles:
        # Report by tile order, not by whichever task lost the race, so the
        # same broken area produces the same error twice running.
        if tile in failures:
            raise failures[tile]

    return dedupe(point for tile in tiles for point in decoded[tile])


async def fetch_tile(client: UpstreamClient, tile: Tile) -> bytes:
    """One tile's bytes; empty where the tileset has nothing to say.

    A 404 here means the area holds no features, not that the endpoint is
    wrong — tile servers do not cut tiles for empty ground. Treating it as a
    failure would break every search whose box touches the sea.
    """
    try:
        return await client.get_bytes(tile_path(tile), accept=MVT_CONTENT_TYPE)
    except UpstreamNotFound:
        logger.debug("no tile at %s", tile_path(tile))
        return b""


def decode_tile(tile: Tile, blob: bytes) -> list[TilePoint]:
    """The features of one tile, as points in lng/lat.

    Decoded with `y_coord_down=True`, which keeps the MVT convention — origin at
    the tile's top-left corner, y growing downward — instead of the library's
    flipped default. One convention, applied where the maths happens.
    """
    if not blob:
        return []

    try:
        layers = mapbox_vector_tile.decode(blob, default_options={"y_coord_down": True})
    except Exception as exc:
        # Anything from a truncated body to a protobuf that is not a tile. The
        # detail stays in the log: the exception text can carry the raw body,
        # and the message below goes straight into a model's context.
        logger.warning("undecodable tile %s: %s", tile_path(tile), exc)
        raise UpstreamSchemaChangedError(
            f"The map tile at zoom {tile.z} for this area is not readable as a "
            f"vector tile ({type(exc).__name__})."
        ) from None

    if not isinstance(layers, dict):
        raise UpstreamSchemaChangedError(
            "A map tile decoded to something that is not a set of layers."
        )

    points: list[TilePoint] = []
    for name in LAYERS:
        layer = layers.get(name)
        if layer:
            points.extend(layer_points(tile, name, layer))
    return points


def layer_points(tile: Tile, name: str, layer: Any) -> list[TilePoint]:
    """One source layer's features. `extent` is the size of the grid its
    positions are written on, and without it none of them mean anything."""
    extent = layer.get("extent") if isinstance(layer, dict) else None
    if not isinstance(extent, int) or extent <= 0:
        raise UpstreamSchemaChangedError(
            f"The `{name}` layer of a map tile declares no usable extent, so its "
            "features cannot be placed on the map."
        )

    project = projector(tile, extent)
    decoded = (tile_point(name, feature, project) for feature in layer.get("features") or [])
    return [point for point in decoded if point is not None]


def tile_point(layer: str, feature: Any, project: Projector) -> TilePoint | None:
    """One decoded feature, or nothing if it cannot be named or placed.

    A feature with neither an identifier nor a usable id cannot be linked to or
    asked about later, and one with no position cannot be ranked by distance.
    Both are dropped rather than sunk: a single odd marker is not a reason to
    fail a search over a hundred tiles.
    """
    if not isinstance(feature, dict):
        return None

    properties = feature.get("properties")
    properties = properties if isinstance(properties, dict) else {}

    ref = feature_ref(properties, feature.get("id"))
    if ref is None:
        logger.debug("tile feature in %s has no usable identity", layer)
        return None

    coordinates = stated_location(properties) or geometry_location(
        feature.get("geometry"), project
    )
    if coordinates is None:
        logger.debug("tile feature %s has no usable position", ref.identifier)
        return None

    return TilePoint(ref=ref, coordinates=coordinates, layer=layer, properties=properties)


def dedupe(points: Iterable[TilePoint]) -> list[TilePoint]:
    """First occurrence of each feature wins.

    A marker near a tile edge is written into both neighbouring tiles, so a
    search over adjacent tiles sees it twice. Identity is the (source,
    identifier) pair — the same key upstream forms as `poiId`.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[TilePoint] = []
    for point in points:
        key = (point.ref.source, point.ref.identifier)
        if key not in seen:
            seen.add(key)
            unique.append(point)
    return unique


# --- the two quirks ----------------------------------------------------------


def feature_ref(properties: dict[str, Any], feature_id: Any) -> FeatureRef | None:
    """The feature's identity, from its properties or out of its MVT id."""
    identifier = properties.get("identifier") or osm_identifier(feature_id)
    if not identifier:
        return None
    source = properties.get("poiSource") or DEFAULT_SOURCE
    return FeatureRef(source=str(source), identifier=str(identifier))


def osm_identifier(feature_id: Any) -> str | None:
    """Unpack an OSM identity from an MVT feature id.

    Ported from `osmTileFeatureToPoiIdentifier` in the site's `poi.service.ts`:
    the tile generator multiplies the OSM id by ten and adds a type digit — 1
    node, 2 way, anything else relation. Nothing in the tile announces this; it
    is only knowable from that code, which is why it is spelled out here.

    Upstream reads the last digit off the id's decimal text. Ids are positive,
    where that and arithmetic agree, and a non-positive id is refused rather
    than guessed at.
    """
    if not isinstance(feature_id, int) or isinstance(feature_id, bool) or feature_id <= 0:
        return None
    kind = {1: "node", 2: "way"}.get(feature_id % 10, "relation")
    return f"{kind}_{feature_id // 10}"


def stated_location(properties: dict[str, Any]) -> Coordinates | None:
    """`poiGeolocation`: where upstream says the feature is.

    Preferred over the drawn geometry, which is quantised to the tile grid and,
    for a line or an area, is not a position at all. MVT properties can only be
    scalars, so this object crosses the wire as a JSON string — the same parse
    the site does in `convertFeatureToPoi`. Anything unparseable falls through
    to the geometry rather than failing the tile.
    """
    raw = properties.get("poiGeolocation")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    try:
        return Coordinates(lat=float(raw["lat"]), lng=float(raw["lng"]))
    except (KeyError, TypeError, ValueError, ValidationError):
        return None


# --- tile grid to lng/lat ----------------------------------------------------


def projector(tile: Tile, extent: int) -> Projector:
    """Turn positions on one tile's grid back into lng/lat.

    The grid is linear in Web Mercator metres, not in degrees — latitude is
    stretched further from the equator — so the interpolation happens in
    metres across the tile's own bounds, and only the last step leaves the
    projection. Interpolating in degrees instead would misplace features by
    tens of metres at this latitude.
    """
    left, bottom, right, top = mercantile.xy_bounds(tile)

    def to_lnglat(px: float, py: float) -> Position:
        x = left + (px / extent) * (right - left)
        y = top - (py / extent) * (top - bottom)
        lng, lat = mercantile.lnglat(x, y)
        return lng, lat

    return to_lnglat


def geometry_location(geometry: Any, project: Projector) -> Coordinates | None:
    """Where a decoded geometry sits, following the site's `getGeolocation`:
    the middle of an area's extent, and otherwise its first vertex."""
    if not isinstance(geometry, dict):
        return None

    grid = positions(geometry.get("coordinates"))
    if geometry.get("type") in AREA_TYPES:
        corners = [project(px, py) for px, py in grid]
        if not corners:
            return None
        lngs = [lng for lng, _ in corners]
        lats = [lat for _, lat in corners]
        located = ((min(lngs) + max(lngs)) / 2, (min(lats) + max(lats)) / 2)
    else:
        first = list(islice(grid, 1))
        if not first:
            return None
        located = project(*first[0])

    try:
        return Coordinates(lng=located[0], lat=located[1])
    except ValidationError:
        return None


def positions(coordinates: Any) -> Iterator[Position]:
    """Every position in a GeoJSON `coordinates` member, in document order.

    Nesting depth is what distinguishes a point from a multipolygon, so this
    recurses until it finds numbers rather than switching on the geometry type.
    """
    if not isinstance(coordinates, (list, tuple)) or not coordinates:
        return
    if isinstance(coordinates[0], (int, float)):
        if len(coordinates) >= 2 and isinstance(coordinates[1], (int, float)):
            yield float(coordinates[0]), float(coordinates[1])
        return
    for part in coordinates:
        yield from positions(part)
