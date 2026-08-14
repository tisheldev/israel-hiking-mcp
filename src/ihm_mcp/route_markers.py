"""What a route marker in the tileset says, and which markers a search keeps.

The map has no route API. A hiking route exists in the tiles as a single point
— the marker the site draws at the route's start — carrying the whole route in
its properties: `poiCategory` says what activity it is for, `poiLength` how long
it is in metres, `poiDifficulty` how hard, when anybody said.

Verified against live tiles on 2026-08-14: those properties arrive on the tile
itself, already computed upstream, so nothing here re-derives a category from
raw OSM tags. `poiDifficulty` is the exception — it reached 2 of 168 features in
the sample, and `RouteConstraints` is built around its usually being absent.

Nothing in this module talks to the network: given decoded points, it is a pure
function to summaries, which is what makes the tool's output testable and its
ordering something a caller can rely on.
"""

from __future__ import annotations

from typing import get_args

from ihm_mcp import tiles
from ihm_mcp.models import (
    Coordinates,
    Difficulty,
    Language,
    Model,
    RouteSummary,
    poi_url,
)
from ihm_mcp.spatial import haversine_km
from ihm_mcp.tiles import TilePoint

#: The `poiCategory` this server's route search is about. The tileset also
#: carries `Bicycle` and `4x4` route markers, which the MVP does not return.
HIKING_CATEGORY = "Hiking"

#: Metres, upstream's unit for `poiLength`.
METRES_PER_KM = 1000.0

#: Distances and lengths are reported to 10 m. The tile grid quantises position
#: to about 2.4 m at zoom 12, so more decimals would be invented precision — and
#: rounding first means a route reported as 12.0 km is one a 12 km limit keeps.
REPORTED_DECIMALS = 2

DIFFICULTIES = frozenset(get_args(Difficulty))


class RouteConstraints(Model):
    """What a caller asked of a route, beyond where it is.

    Every constraint is optional, and each is applied to the value this server
    reports, so a result never contradicts the filter that admitted it. The
    field names are the tool's own argument names, so an error message about
    one of them names the thing the caller actually typed.
    """

    minLengthKm: float | None = None
    maxLengthKm: float | None = None
    difficulty: Difficulty | None = None

    def keeps(self, route: RouteSummary) -> bool:
        return self.length_fits(route.lengthKm) and self.difficulty_fits(route.difficulty)

    def length_fits(self, length_km: float | None) -> bool:
        """A route of unknown length cannot be shown to satisfy a length range,
        so it is kept only while no range was asked for."""
        if self.minLengthKm is None and self.maxLengthKm is None:
            return True
        if length_km is None:
            return False
        return (self.minLengthKm is None or length_km >= self.minLengthKm) and (
            self.maxLengthKm is None or length_km <= self.maxLengthKm
        )

    def difficulty_fits(self, difficulty: Difficulty | None) -> bool:
        """An unrated route passes any difficulty filter.

        This is the map site's own rule (`getPublicRoutes` skips the check when
        the property is missing), and dropping unrated routes instead would hide
        almost every OSM route: the rating reaches only a handful of them. The
        tool says so in its warnings, and each result carries a null rating.
        """
        return (
            self.difficulty is None
            or difficulty is None
            or difficulty == self.difficulty
        )


def is_hiking_marker(point: TilePoint) -> bool:
    """Whether this tile feature is a hiking route rather than a point of
    interest or a route for another activity."""
    return tiles.text(point.properties, "poiCategory") == HIKING_CATEGORY


def within_radius(
    points: list[TilePoint], center: Coordinates, radius_km: float
) -> list[TilePoint]:
    """The markers that really are inside the circle.

    The tiles covering a circle also cover ground outside it, so the radius a
    caller asked for is a separate question from which tiles were fetched. The
    distance is rounded the way it will be reported, so a route listed at
    exactly the radius is one the search kept.
    """
    return [
        point
        for point in points
        if reported_km(haversine_km(center, point.coordinates)) <= radius_km
    ]


def route_summary(
    point: TilePoint, *, center: Coordinates, base_url: str, language: Language
) -> RouteSummary | None:
    """One marker as a route, or nothing when the map does not label it.

    An unnamed route is dropped rather than given an invented title: the map
    site hides those features too, and a caller cannot ask a person about a
    route that has no name to say.
    """
    name = tiles.title(point.properties, language)
    if not name:
        return None

    note = tiles.description(point.properties, language)
    return RouteSummary(
        ref=point.ref,
        title=name,
        description=note or None,
        difficulty=difficulty_of(point.properties),
        lengthKm=length_km(point.properties),
        startPoint=point.coordinates,
        distanceFromSearchCenterKm=reported_km(haversine_km(center, point.coordinates)),
        ihmUrl=poi_url(base_url, point.ref, language),
    )


def difficulty_of(properties: tiles.Properties) -> Difficulty | None:
    """The rated difficulty, if it is one this server knows how to mean.

    A value outside upstream's own scale is reported as no rating at all: a
    grade nobody can interpret is worse than an honest null.
    """
    rating = tiles.text(properties, "poiDifficulty")
    return rating if rating in DIFFICULTIES else None  # type: ignore[return-value]


def length_km(properties: tiles.Properties) -> float | None:
    """`poiLength` in kilometres, or None where the map holds no length.

    Upstream writes zero for a feature with nothing to measure — 141 of the 168
    features in the sampled tile, every one of them a point of interest rather
    than a route — so zero is an absent length, not a route of no length.
    """
    metres = tiles.number(properties, "poiLength")
    if metres is None or metres <= 0:
        return None
    return reported_km(metres / METRES_PER_KM)


def reported_km(kilometres: float) -> float:
    """A distance as this server states it. Every kilometre figure that reaches
    a caller — or a filter — goes through here, so the two always agree."""
    return round(kilometres, REPORTED_DECIMALS)


def nearest_first(routes: list[RouteSummary]) -> list[RouteSummary]:
    """Routes ordered nearest to the search centre first.

    Ties break on the route's identity, so the same search returns the same
    order every time — two markers can sit at one point, and an order that
    depends on which tile answered first is not something to evaluate against.
    """
    return sorted(
        routes,
        key=lambda route: (
            route.distanceFromSearchCenterKm,
            route.ref.source,
            route.ref.identifier,
        ),
    )
