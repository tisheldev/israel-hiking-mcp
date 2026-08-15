"""Reading map features out of vector tiles.

The markers this server searches — routes and points of interest — have no
query API. They exist only as the pre-rendered tiles the map site itself draws:
`GET /vector/data/global_points/{z}/{x}/{y}.mvt`, zoom 10 to 14. So "what is
near this point" becomes a job per module: work out which tiles cover the area
(`grid`), fetch them (`reader`), decode the Mapbox Vector Tile format back into
coordinates (`decode`), and read the values a feature carries out of upstream's
own property bag (`properties`).

The fan-out is bounded. Tile count grows with the square of the radius, and
this server will not spend hundreds of requests on a volunteer-run map without
telling the caller what it is about to cost.
"""

from ihm_mcp.tiles.decode import (
    AREA_TYPES,
    DEFAULT_SOURCE,
    LAYERS,
    TilePoint,
    decode_tile,
    layer_points,
    osm_identifier,
    stated_location,
)
from ihm_mcp.tiles.grid import (
    DEFAULT_ZOOM,
    MAX_ZOOM,
    MIN_BUFFER_METERS,
    MIN_ZOOM,
    Tile,
    bounding_box,
    corridor_tiles,
    covering_tiles,
    reachable_tiles,
    tile_box,
    tile_path,
    tiles_for_corridor,
    tiles_for_radius,
)
from ihm_mcp.tiles.properties import (
    NAME_KEYS,
    Properties,
    description,
    number,
    text,
    title,
)
from ihm_mcp.tiles.reader import (
    TileFetcher,
    points_along_corridor,
    points_in_radius,
    points_in_tiles,
)

__all__ = [
    "AREA_TYPES",
    "DEFAULT_SOURCE",
    "DEFAULT_ZOOM",
    "LAYERS",
    "MAX_ZOOM",
    "MIN_BUFFER_METERS",
    "MIN_ZOOM",
    "NAME_KEYS",
    "Properties",
    "Tile",
    "TileFetcher",
    "TilePoint",
    "bounding_box",
    "corridor_tiles",
    "covering_tiles",
    "decode_tile",
    "description",
    "layer_points",
    "number",
    "osm_identifier",
    "points_along_corridor",
    "points_in_radius",
    "points_in_tiles",
    "reachable_tiles",
    "stated_location",
    "text",
    "tile_box",
    "tile_path",
    "tiles_for_corridor",
    "tiles_for_radius",
    "title",
]
