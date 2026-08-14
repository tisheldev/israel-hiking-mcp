"""Distances on the earth, for the numbers a caller is shown.

Kept apart from `tiles.grid`, which has a flat-earth distance of its own: that
one picks tiles and may err towards fetching one too many, while these numbers
are reported, compared against a caller's radius, and sorted on.
"""

from __future__ import annotations

import math

from ihm_mcp.models import Coordinates

#: Mean earth radius (IUGG). The spherical assumption costs about 0.3% at worst,
#: which at this server's scale is metres.
EARTH_RADIUS_KM = 6371.0088


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
