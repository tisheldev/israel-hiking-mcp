"""What a point of interest in the tileset says, and which ones a search keeps.

The tiles are the only index of these features there is, the same as for routes
— but where a route marker carries measurements, a POI marker carries a
classification: `poiCategory` says what kind of thing it is and `poiIcon` says
which of that kind.

**The classification is upstream's, not this server's.** The plan for this
module was to port `osm-tags.service.ts` — the map site's own `natural=spring` →
Water mapping — but a live zoom-12 tile settles the question: sampled over Haifa
on 2026-08-15, every feature carried `poiCategory`, `poiIcon` and `poiIconColor`
and **not one raw OSM tag**. The tag mapping has already run by the time the
tileset is cut, so re-deriving it here would need tags this server never sees,
and would produce a second opinion where upstream has already published one.
What is ported instead is the naming: `poiIcon` back to the label the map site
puts on that icon in its own POI category list.

Nothing here talks to the network. Given decoded points and a route's corridor,
it is a pure function to results, which is what makes the ordering something a
caller can rely on.
"""

from __future__ import annotations

from ihm_mcp import tiles
from ihm_mcp.models import (
    MAPPED_FEATURE,
    POI_CATEGORIES,
    Language,
    Model,
    PoiAlongRoute,
    PoiCategory,
    poi_url,
)
from ihm_mcp.spatial import Corridor
from ihm_mcp.tiles import TilePoint

#: `poiIcon` to the label the map site itself puts on that icon, from
#: `POINTS_OF_INTEREST_CATEGORIES` in `initial-state.ts` where it names one and
#: from what `setIconColorCategory` maps where it does not. The map's own words
#: throughout — a subtype invented here would be a claim about the feature that
#: nothing upstream makes.
SUBTYPES = {
    # Water. `icon-tint` covers springs, ponds and plain `natural=water` alike;
    # the map site draws one icon for all three and so names them together.
    "icon-tint": "Spring, Pond",
    "icon-waterfall": "Waterfall",
    "icon-waterhole": "Waterhole",
    "icon-water-well": "Water Well",
    "icon-cistern": "Cistern",
    # `icon-river` is not in the site's list of POIs a user can add, because it
    # is drawn for whole waterways rather than for a point somebody visits.
    "icon-river": "Stream, River",
    # Historic.
    "icon-ruins": "Ruins",
    "icon-archaeological": "Archaeological Site",
    "icon-memorial": "Memorial",
    # Viewpoint, Camping, Natural.
    "icon-viewpoint": "Viewpoint",
    "icon-picnic": "Picnic Area",
    "icon-campsite": "Campsite",
    "icon-alpinehut": "Alpine Hut",
    "icon-cave": "Cave",
    "icon-tree": "Tree",
    "icon-flowers": "Flowers",
    "icon-peak": "Peak, Ridge, Valley",
    # Other, which is upstream's bucket for everything its mapping does not
    # place — down to `icon-search`, the fallback for a feature it recognised
    # only well enough to draw, and deliberately left unnamed here.
    "icon-star": "Attraction",
    "icon-artwork": "Artwork",
    # `place=*`, which upstream draws the same whether it is a city, a hamlet
    # or a named square — so the label says no more than the tag does.
    "icon-home": "Place",
    "icon-leaf": "Nature Reserve, National Park",
    "icon-bed": "Accommodation",
    "icon-synagogue": "Synagogue",
    "icon-church": "Church",
    "icon-mosque": "Mosque",
    "icon-holy-place": "Place of Worship",
    "icon-inature": "iNature Entry",
    "icon-wikipedia-w": "Wikipedia Entry",
}

#: What every water result has to be read with. A mapped water feature is a
#: record that somebody once saw water there, and nothing more: this server
#: cannot tell a perennial spring from a winter puddle, and a hiker planning
#: around one of these is planning around an undated note.
WATER_CAUTION = (
    "Mapped water feature — not known to be flowing, reachable, permitted or "
    "safe to drink. Do not plan water on this. Carry what the walk needs."
)

WATER = "Water"

#: What this data cannot answer about any point near a route, whatever its
#: category. Returned whole with every result, because silence about these
#: reads as reassurance.
POI_UNKNOWNS = [
    (
        "Whether any water shown here is flowing today, and whether it is "
        "reachable, permitted or safe to drink. Springs and pools in this "
        "country are seasonal, and none of these records carry a date."
    ),
    (
        "Whether a feature still exists, and whether it can be reached from the "
        "route — the distance given is a straight line over whatever lies "
        "between, which may be a cliff, a fence, private land or a firing zone."
    ),
    (
        "Whether any of these are open, staffed, free, or permitted to enter, "
        "and whether a campsite or picnic area allows fires or overnight stays."
    ),
    (
        "Everything the map does not hold at all. An empty result means nothing "
        "of that kind is mapped near this route, not that nothing is there."
    ),
]


class PoiConstraints(Model):
    """What a caller asked of a point, beyond where it is.

    Field names are the tool's own argument names, so an error or a warning
    about one of them names the thing the caller actually typed.
    """

    categories: frozenset[PoiCategory]
    bufferMeters: float

    def keeps(self, category: PoiCategory, distance_meters: int) -> bool:
        """Applied to the distance as reported, not the one measured, so a point
        listed at 500 m is one a 500 m buffer keeps."""
        return category in self.categories and distance_meters <= self.bufferMeters


def is_poi_marker(point: TilePoint) -> bool:
    """Whether this tile feature is a point of interest rather than a route.

    The same tiles carry the route markers `search_hiking_routes` reads, under
    categories of their own. A feature whose category is one this server does
    not know is dropped rather than filed under `Other`: `Other` is upstream's
    own word for a feature it placed, and guessing it here would put an
    unrecognised category behind a caller's back.
    """
    return tiles.text(point.properties, "poiCategory") in POI_CATEGORIES


def category_of(point: TilePoint) -> PoiCategory:
    """The map's own category for this marker. Only call for a POI marker."""
    return tiles.text(point.properties, "poiCategory")  # type: ignore[return-value]


def subtype_of(point: TilePoint) -> str | None:
    """The map site's label for the icon it draws here, where it has one."""
    return SUBTYPES.get(tiles.text(point.properties, "poiIcon"))


def caution_for(category: PoiCategory) -> str | None:
    """The note that has to travel with a result of this kind."""
    return WATER_CAUTION if category == WATER else None


def in_corridor(
    points: list[TilePoint], corridor: Corridor, constraints: PoiConstraints
) -> list[tuple[TilePoint, int]]:
    """The markers a caller asked about, each with how far off the route it is.

    Distance is rounded here, once, and everything downstream — the filter, the
    sort, the reported number — uses that one value. Whole metres because the
    tile grid quantises position to about 2.4 m at zoom 12, so a decimal would
    be precision this server does not have.
    """
    measured = (
        (point, round(corridor.metres_to(point.coordinates))) for point in points
    )
    return [
        (point, metres)
        for point, metres in measured
        if constraints.keeps(category_of(point), metres)
    ]


def poi_along_route(
    point: TilePoint,
    distance_meters: int,
    *,
    base_url: str,
    language: Language,
) -> PoiAlongRoute | None:
    """One marker as a result, or nothing when the map does not name it.

    An unnamed feature is dropped rather than titled by its category: "Cistern"
    is what kind of thing it is, not what it is called, and a caller cannot ask
    anybody about a point that has no name to say. How many were dropped goes
    into the tool's warnings, so the omission is visible.
    """
    name = tiles.title(point.properties, language)
    if not name:
        return None

    category = category_of(point)
    note = tiles.description(point.properties, language)
    return PoiAlongRoute(
        ref=point.ref,
        title=name,
        description=note or None,
        category=category,
        subtype=subtype_of(point),
        coordinates=point.coordinates,
        distanceFromRouteMeters=distance_meters,
        evidence=MAPPED_FEATURE,
        caution=caution_for(category),
        ihmUrl=poi_url(base_url, point.ref, language),
    )


def nearest_first(pois: list[PoiAlongRoute]) -> list[PoiAlongRoute]:
    """Points ordered nearest to the route first.

    Ties break on identity, so the same route and buffer return the same list
    in the same order — several markers can sit within a metre of each other,
    and an order that depends on which tile answered first is not something to
    evaluate against.
    """
    return sorted(
        pois,
        key=lambda poi: (
            poi.distanceFromRouteMeters,
            poi.ref.source,
            poi.ref.identifier,
        ),
    )


def line_features(pois: list[PoiAlongRoute]) -> list[PoiAlongRoute]:
    """The results whose marker stands for something longer than a point.

    A stream is mapped as a line and marked at one end of it, so its distance
    is the distance to that mark rather than to wherever the route crosses the
    water. Worth saying out loud, because a stream 3 km away by this measure
    may run under the trail's own bridge.
    """
    return [poi for poi in pois if poi.subtype == SUBTYPES["icon-river"]]


def water_in(pois: list[PoiAlongRoute]) -> list[PoiAlongRoute]:
    return [poi for poi in pois if poi.category == WATER]
