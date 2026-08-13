"""Reading map features out of Mapbox Vector Tile bytes.

A feature's position is a pair of integers on a 0–`extent` grid anchored to its
own tile, so placing one needs that tile's Web Mercator bounds — and the grid
quantises, to roughly 2.4 m at zoom 12.

Two upstream conventions are transcribed from the map site's `poi.service.ts`,
the only place they are written down: OSM identity is packed into the MVT
feature id, and `poiGeolocation` travels as a JSON string because MVT
properties can only be scalars.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import mapbox_vector_tile
import mercantile
from pydantic import ValidationError

from ihm_mcp.errors import UpstreamSchemaChangedError
from ihm_mcp.models import Coordinates, FeatureRef, Model
from ihm_mcp.tiles.grid import Tile, tile_path

logger = logging.getLogger(__name__)

Position = tuple[float, float]
Projector = Callable[[float, float], Position]

#: Both source layers in the tileset. `external` holds the non-OSM datasets
#: (Nakeb, iNature), searchable here even where later tools cannot resolve them.
LAYERS = ("global_points", "external")

#: What upstream assumes when a feature does not name its dataset.
DEFAULT_SOURCE = "OSM"

#: Geometry types placed at the middle of their extent rather than at their
#: first vertex, matching `getGeolocation` in `poi.service.ts`.
AREA_TYPES = ("Polygon", "MultiPolygon")


class TilePoint(Model):
    """One marker decoded from a tile, before any tool decides what it means.

    `properties` is upstream's own bag, passed through unedited: a tool reads
    `poiCategory`, `poiLength`, `name:he` and friends out of it.
    """

    ref: FeatureRef
    coordinates: Coordinates
    layer: str
    properties: dict[str, Any]


def decode_tile(tile: Tile, blob: bytes) -> list[TilePoint]:
    """The features of one tile, as points in lng/lat."""
    if not blob:
        return []

    try:
        # `y_coord_down` keeps the MVT convention — origin at the tile's
        # top-left, y growing downward — over the library's flipped default.
        layers = mapbox_vector_tile.decode(blob, default_options={"y_coord_down": True})
    except Exception as exc:
        # The detail stays in the log: the exception text can carry the raw
        # body, and the message below goes straight into a model's context.
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

    Dropped rather than fatal: a single odd marker is not a reason to fail a
    search over a hundred tiles.
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


def feature_ref(properties: dict[str, Any], feature_id: Any) -> FeatureRef | None:
    """The feature's identity, from its properties or out of its MVT id."""
    identifier = properties.get("identifier") or osm_identifier(feature_id)
    if not identifier:
        return None
    source = properties.get("poiSource") or DEFAULT_SOURCE
    return FeatureRef(source=str(source), identifier=str(identifier))


def osm_identifier(feature_id: Any) -> str | None:
    """Unpack an OSM identity from an MVT feature id.

    The tile generator multiplies the OSM id by ten and adds a type digit — 1
    node, 2 way, anything else relation. Ids are positive; anything else is
    refused rather than guessed at.
    """
    if not isinstance(feature_id, int) or isinstance(feature_id, bool) or feature_id <= 0:
        return None
    kind = {1: "node", 2: "way"}.get(feature_id % 10, "relation")
    return f"{kind}_{feature_id // 10}"


def stated_location(properties: dict[str, Any]) -> Coordinates | None:
    """`poiGeolocation`: where upstream says the feature is.

    Preferred over the drawn geometry, which is quantised to the tile grid and,
    for a line or an area, is not a position at all. Anything unparseable falls
    through to the geometry.
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


def geometry_location(geometry: Any, project: Projector) -> Coordinates | None:
    """Where a decoded geometry sits, following the site's `getGeolocation`:
    the middle of an area's extent, and otherwise its first vertex."""
    if not isinstance(geometry, dict):
        return None

    grid = positions(geometry.get("coordinates"))
    projected = (project(px, py) for px, py in grid)
    if geometry.get("type") in AREA_TYPES:
        located = extent_center(projected)
    else:
        located = next(projected, None)
    if located is None:
        return None

    try:
        return Coordinates(lng=located[0], lat=located[1])
    except ValidationError:
        return None


def extent_center(points: Iterable[Position]) -> Position | None:
    """The middle of the box covering `points`."""
    lngs: list[float] = []
    lats: list[float] = []
    for lng, lat in points:
        lngs.append(lng)
        lats.append(lat)
    if not lngs:
        return None
    return (min(lngs) + max(lngs)) / 2, (min(lats) + max(lats)) / 2


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


def projector(tile: Tile, extent: int) -> Projector:
    """Turn positions on one tile's grid back into lng/lat.

    The grid is linear in Web Mercator metres, not in degrees, so interpolating
    in degrees would misplace features by tens of metres at this latitude.
    """
    left, bottom, right, top = mercantile.xy_bounds(tile)

    def to_lnglat(px: float, py: float) -> Position:
        x = left + (px / extent) * (right - left)
        y = top - (py / extent) * (top - bottom)
        lng, lat = mercantile.lnglat(x, y)
        return lng, lat

    return to_lnglat
