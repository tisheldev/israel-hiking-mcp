"""Distances and shapes, for the numbers and lines a caller is shown.

Two jobs live here. Distance on the earth — kept apart from `tiles.grid`, which
has a flat-earth distance of its own: that one picks tiles and may err towards
fetching one too many, while these numbers are reported, compared against a
caller's radius, and sorted on. And the geometry of a route: assembling the
line, and cutting it down to something a model can actually read.

Anything metric is computed in a **local equirectangular** frame — degrees
scaled to metres around the latitude in question. At this country's extent the
error is a fraction of a percent, which is far below the precision anything
here claims, and it costs no projection library.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

import shapely

from ihm_mcp.errors import GeometryTooLargeError
from ihm_mcp.models import (
    Coordinates,
    GeometryDetail,
    LineString,
    MultiLineString,
    Position,
    RouteGeometry,
)

#: Mean earth radius (IUGG). The spherical assumption costs about 0.3% at worst,
#: which at this server's scale is metres.
EARTH_RADIUS_KM = 6371.0088

#: Metres in a kilometre, which is also upstream's unit for every length it
#: publishes — tile `poiLength` and a shared route's `length` alike.
METRES_PER_KM = 1000.0

#: Distances and lengths are reported to 10 m. The tile grid quantises position
#: to about 2.4 m at zoom 12, so more decimals would be invented precision — and
#: rounding first means a route reported as 12.0 km is one a 12 km limit keeps.
REPORTED_DECIMALS = 2

#: Positions are reported to six decimals, about 0.11 m here. A recorded track
#: is nowhere near that accurate, and the digits past it are float noise that a
#: caller would otherwise pay for by the token.
COORDINATE_DECIMALS = 6

#: How many positions a route's geometry may carry before it is thinned. A
#: recorded track can hold tens of thousands, which is a wall of numbers no
#: reader — human or model — gets anything from.
MAX_GEOMETRY_POINTS = 3_000

#: Tried in order, coarsest last: the first tolerance that brings a route under
#: the cap is the one used, so a route is thinned as little as it needs to be.
SIMPLIFY_TOLERANCES_M = (10.0, 25.0, 50.0, 100.0)


def haversine_km(origin: Coordinates, point: Coordinates) -> float:
    """Great-circle distance between two points, in kilometres.

    Straight-line over the ground, so it is a lower bound on any walk: a route
    2 km from here is at least 2 km away, and usually further by road or trail.
    """
    origin_lat, point_lat = math.radians(origin.lat), math.radians(point.lat)
    delta_lat = point_lat - origin_lat
    delta_lng = math.radians(point.lng - origin.lng)

    # Haversine rather than the spherical law of cosines: this form keeps its
    # precision for the short distances an area search actually deals in.
    haversine_of_angle = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(origin_lat) * math.cos(point_lat) * math.sin(delta_lng / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(haversine_of_angle))


def reported_km(kilometres: float) -> float:
    """A distance as this server states it. Every kilometre figure that reaches
    a caller — or a filter — goes through here, so the two always agree."""
    return round(kilometres, REPORTED_DECIMALS)


# --- assembling a line -------------------------------------------------------


def positions(points: Iterable[Coordinates]) -> list[Position]:
    """Coordinates as GeoJSON positions: longitude first, and no repeats.

    A shared route arrives in segments that each restate the previous one's last
    point, so a plain concatenation carries every junction twice. The duplicates
    are geometrically meaningless and would count against the size budget.
    """
    line: list[Position] = []
    for point in points:
        position = (
            round(point.lng, COORDINATE_DECIMALS),
            round(point.lat, COORDINATE_DECIMALS),
        )
        if not line or line[-1] != position:
            line.append(position)
    return line


def rounded(point: Coordinates) -> Coordinates:
    """A point stated to the same precision as the lines around it. Upstream
    sends fourteen digits; the last eight of them are float noise."""
    return Coordinates(
        lat=round(point.lat, COORDINATE_DECIMALS),
        lng=round(point.lng, COORDINATE_DECIMALS),
    )


def geometry_of(lines: Sequence[Sequence[Position]]) -> RouteGeometry | None:
    """Lines as one GeoJSON geometry, or nothing if none of them is a line.

    A single line stays a `LineString`: wrapping every route in a
    `MultiLineString` for the sake of the rare multi-line one would make the
    common case harder to read for no gain.
    """
    drawable = [list(line) for line in lines if len(line) >= 2]
    if not drawable:
        return None
    if len(drawable) == 1:
        return LineString(coordinates=drawable[0])
    return MultiLineString(coordinates=drawable)


def lines_of(geometry: RouteGeometry) -> list[list[Position]]:
    """A geometry's lines, however many it draws them as."""
    if isinstance(geometry, LineString):
        return [list(geometry.coordinates)]
    return [list(line) for line in geometry.coordinates]


def point_count(geometry: RouteGeometry) -> int:
    return sum(len(line) for line in lines_of(geometry))


def start_of(geometry: RouteGeometry) -> Coordinates:
    """Where the route's line begins."""
    lng, lat = lines_of(geometry)[0][0]
    return Coordinates(lat=lat, lng=lng)


# --- cutting it down to size -------------------------------------------------


def metres_per_degree(latitude: float) -> tuple[float, float]:
    """How far a degree of longitude and of latitude reach at this latitude.

    The local equirectangular frame this module measures in: east-west shrinks
    with the cosine of the latitude, north-south does not.
    """
    per_degree = EARTH_RADIUS_KM * METRES_PER_KM * math.pi / 180
    return per_degree * math.cos(math.radians(latitude)), per_degree


def middle_latitude(geometry: RouteGeometry) -> float:
    """The latitude to scale this geometry's metres by — the middle of its
    extent, so the error is split between its ends rather than piled at one."""
    latitudes = [lat for line in lines_of(geometry) for _, lat in line]
    return (min(latitudes) + max(latitudes)) / 2


def simplify_line(
    line: Sequence[Position], tolerance_meters: float, *, latitude: float
) -> list[Position]:
    """Douglas-Peucker: drop the positions that say nothing about the shape.

    Every position kept is one the recorded line really passed through — the
    algorithm chooses among them rather than averaging — and the first and last
    always survive, so a simplified route still starts and ends where it did.
    """
    if len(line) < 3:
        return list(line)

    east, north = metres_per_degree(latitude)
    metric = shapely.LineString([(lng * east, lat * north) for lng, lat in line])
    # `preserve_topology=False` is plain Douglas-Peucker. The guard it turns off
    # keeps a line from crossing itself, which a route is free to do anyway.
    thinned = metric.simplify(tolerance_meters, preserve_topology=False)
    return [
        (round(x / east, COORDINATE_DECIMALS), round(y / north, COORDINATE_DECIMALS))
        for x, y in thinned.coords
    ]


def simplify_geometry(
    geometry: RouteGeometry, tolerance_meters: float
) -> RouteGeometry:
    """The whole geometry thinned at one tolerance, lines and all."""
    latitude = middle_latitude(geometry)
    thinned = [
        simplify_line(line, tolerance_meters, latitude=latitude)
        for line in lines_of(geometry)
    ]
    # Simplification only ever removes interior positions, so a line that was
    # drawable stays drawable and there is always a geometry to return.
    simplified = geometry_of(thinned)
    assert simplified is not None
    return simplified


def fit_geometry(
    geometry: RouteGeometry,
    *,
    max_points: int = MAX_GEOMETRY_POINTS,
    tolerances: Sequence[float] = SIMPLIFY_TOLERANCES_M,
) -> tuple[RouteGeometry, GeometryDetail]:
    """A geometry small enough to hand over, and the record of what that cost.

    Routes are returned unthinned wherever they fit, and thinned by the least
    tolerance that brings them under the cap. A route still too detailed at the
    coarsest tolerance is refused rather than truncated: half a route is a
    different route, and a caller reading the end of it would never know.
    """
    recorded = point_count(geometry)
    if recorded <= max_points:
        return geometry, GeometryDetail(
            pointCount=recorded,
            recordedPointCount=recorded,
            simplified=False,
            toleranceMeters=None,
        )

    for tolerance in tolerances:
        simplified = simplify_geometry(geometry, tolerance)
        kept = point_count(simplified)
        if kept <= max_points:
            return simplified, GeometryDetail(
                pointCount=kept,
                recordedPointCount=recorded,
                simplified=True,
                toleranceMeters=tolerance,
            )

    raise GeometryTooLargeError(
        f"This route's shape holds {recorded:,} positions, and still exceeds the "
        f"{max_points:,} this server returns after simplifying to "
        f"{tolerances[-1]:g} m. Open it on the map site instead."
    )
