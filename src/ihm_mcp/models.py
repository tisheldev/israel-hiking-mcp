"""The vocabulary every tool answers in.

These models are the contract an LLM sees: field names and descriptions travel
to the client in each tool's output schema, so they are written to be read by a
model, not only by a Python caller. Names are camelCase to match the upstream
API and the URLs they appear in.

Two rules the rest of the server leans on:

* A feature is identified by a `FeatureRef`, never a bare id. `123456` is
  meaningless without knowing it is an OSM relation.
* Every result carries its `Attribution`. The upstream licences require it, and
  a model that is handed data with no provenance will invent some.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, get_args
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from ihm_mcp import ATTRIBUTION

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]

#: The two languages the map site publishes names and descriptions in.
Language = Literal["he", "en"]

#: Upstream's own difficulty scale, from the map site's route filter. Only some
#: routes carry one — see `RouteSummary.difficulty`.
Difficulty = Literal["Easy", "Moderate", "Hard", "Very Hard"]

DIFFICULTIES = frozenset(get_args(Difficulty))


def known_difficulty(value: Any) -> Difficulty | None:
    """A rating this server knows how to mean, or nothing.

    Both places a difficulty comes from — a tile property and a shared route's
    own field — can carry a grade outside upstream's scale, and upstream itself
    writes the literal string "Unknown" when a route has none. A grade nobody
    can interpret is worse than an honest null.
    """
    return value if value in DIFFICULTIES else None  # type: ignore[return-value]


#: What a route is for, in the map site's own words. The same three the tiles
#: label a route marker with and `ResolvedRoute.activity` reports — that field
#: is a plain string because a source may name something else entirely, while
#: this is the closed set a caller can *ask* to be routed for.
Activity = Literal["Hiking", "Bicycle", "4x4"]

#: The map's own categories for a point of interest, as its tiles publish them.
#: `Hiking`, `Bicycle` and `4x4` are categories too, but they label routes
#: rather than points, and `search_hiking_routes` is where those belong.
PoiCategory = Literal["Water", "Natural", "Historic", "Viewpoint", "Camping", "Other"]

POI_CATEGORIES = frozenset(get_args(PoiCategory))


class Model(BaseModel):
    """Base for tool inputs and outputs: immutable once built."""

    model_config = ConfigDict(frozen=True)


class Coordinates(Model):
    """A single WGS84 point."""

    lat: Latitude = Field(description="Latitude in decimal degrees (WGS84).")
    lng: Longitude = Field(description="Longitude in decimal degrees (WGS84).")


class BoundingBox(Model):
    """An axis-aligned lat/lng box. Not valid across the antimeridian, which
    the area this server covers never approaches."""

    minLat: Latitude = Field(description="Southern edge, decimal degrees.")
    minLng: Longitude = Field(description="Western edge, decimal degrees.")
    maxLat: Latitude = Field(description="Northern edge, decimal degrees.")
    maxLng: Longitude = Field(description="Eastern edge, decimal degrees.")

    def contains(self, point: Coordinates) -> bool:
        return (
            self.minLat <= point.lat <= self.maxLat
            and self.minLng <= point.lng <= self.maxLng
        )


#: Roughly the extent the Israel Hiking Map covers. It is a box, so it also
#: takes in slivers of neighbouring countries and leaves out nothing that
#: matters here; it exists to rank a worldwide search, not to draw a border.
ISRAEL_BBOX = BoundingBox(minLat=29.3, minLng=34.2, maxLat=33.4, maxLng=35.9)


class FeatureRef(Model):
    """How this server names a map feature — the handle other tools accept.

    Later PRs specialise this for routes and POIs; the pair itself never
    changes shape.
    """

    source: str = Field(
        description="Dataset the feature comes from, e.g. 'OSM'. Sources other "
        "than OSM exist upstream and are not all resolvable by this server."
    )
    identifier: str = Field(
        description="Identifier within that source, e.g. 'relation_282071'. "
        "Meaningful only together with `source`."
    )


def poi_url(base_url: str, ref: FeatureRef, language: Language) -> str:
    """The map site's page for a feature — the link a person can actually open.

    One definition, because every tool that returns a feature returns this link
    and a second spelling of the format would drift from the first.
    """
    source = quote(ref.source, safe="")
    identifier = quote(ref.identifier, safe="")
    return f"{base_url.rstrip('/')}/poi/{source}/{identifier}?language={language}"


def share_url(base_url: str, share_id: str) -> str:
    """The map site's page for a shared route.

    A share has its own URL space — `/share/{id}`, not `/poi/{source}/{id}` —
    because it is a route somebody saved rather than a feature on the map.
    """
    return f"{base_url.rstrip('/')}/share/{quote(share_id, safe='')}"


class Attribution(Model):
    """Credit that must travel with any use of the data in a result."""

    notice: str = Field(description="Credit line to reproduce alongside this data.")
    sources: list[str] = Field(description="Each contributing dataset and its licence.")


IHM_ATTRIBUTION = Attribution(
    notice=ATTRIBUTION,
    sources=[
        "Israel Hiking Map / Mapeak (https://mapeak.com) — CC BY-NC-SA 3.0",
        "OpenStreetMap contributors (https://www.openstreetmap.org/copyright) — ODbL",
    ],
)


class PlaceResult(Model):
    """One candidate location for a searched name."""

    ref: FeatureRef = Field(description="Identity of the matched feature.")
    title: str = Field(description="The feature's own name in the requested language.")
    displayName: str = Field(
        description="Name with its administrative context, e.g. "
        "'Haifa, Haifa Subdistrict, Israel'. Use this to disambiguate."
    )
    coordinates: Coordinates = Field(
        description="A representative point for the feature — not a trailhead, "
        "entrance, or parking area."
    )
    inIsrael: bool = Field(
        description="Whether the point falls inside the approximate box this "
        "server uses for Israel. Box-shaped, so treat edges as uncertain."
    )
    hasExtraData: bool = Field(
        description="Upstream flag: the map holds extended data (description, "
        "images) for this feature. Not a promise that any tool can resolve it."
    )
    ihmUrl: str = Field(description="Human-viewable page for this feature on the map site.")


class SearchedArea(Model):
    """The circle a search actually covered, echoed back so a caller can see
    what a result is a result *of*."""

    center: Coordinates = Field(description="Point the search was centred on.")
    radiusKm: float = Field(description="Radius searched around that point, in kilometres.")


class RouteSummary(Model):
    """One mapped hiking route, as an area search can describe it.

    Everything here comes from the map's own marker for the route. It says what
    somebody recorded, not what is true on the ground today: nothing in it
    establishes that the route is open, marked, passable, permitted, or safe.
    """

    ref: FeatureRef = Field(
        description="Identity of the route. Pass this to a tool that resolves "
        "route details; not every source can be resolved."
    )
    title: str = Field(description="The route's name in the requested language.")
    description: str | None = Field(
        description="The mapper's own note about the route, when there is one. "
        "Often the only place a closure or a hazard is recorded — and undated, "
        "so it may describe conditions from years ago."
    )
    difficulty: Difficulty | None = Field(
        description="Difficulty as rated upstream. Null for most routes: the "
        "rating is optional in the map data, and its absence says nothing about "
        "how hard the route is."
    )
    lengthKm: float | None = Field(
        description="Length of the mapped route in kilometres, rounded to 10 m. "
        "Null when the map data carries no length."
    )
    startPoint: Coordinates = Field(
        description="Where the map draws the route's start marker. Not a "
        "verified trailhead, parking place, or public access point."
    )
    distanceFromSearchCenterKm: float = Field(
        description="Great-circle distance from the search centre to "
        "`startPoint`, rounded to 10 m. Not a walking distance."
    )
    ihmUrl: str = Field(description="Human-viewable page for this route on the map site.")


class Evidence(Model):
    """What kind of claim a result is, and how old it might be.

    Every point this server returns is the same kind: somebody mapped a feature
    at some point, and nothing recorded when. The fields are constant rather
    than computed, and they are here to be read — a result with no provenance
    beside it gets treated as an observation, which none of this is.
    """

    kind: Literal["mapped_feature"] = Field(
        default="mapped_feature",
        description="What this result rests on. `mapped_feature`: somebody "
        "recorded this feature in map data. Not a sighting, a survey, or a "
        "report from anyone who has been there recently.",
    )
    observedAt: None = Field(
        default=None,
        description="When the feature was last observed. Always null — the map "
        "data carries no observation date, and this field exists to say so "
        "rather than to leave the question unasked.",
    )
    freshnessKnown: Literal[False] = Field(
        default=False,
        description="Always false. Nothing here establishes how old the mapping "
        "is; it may be from last week or from fifteen years ago.",
    )


#: The only evidence any result here rests on. One shared instance, because it
#: is the same claim every time and the model is frozen.
MAPPED_FEATURE = Evidence()


class PoiAlongRoute(Model):
    """One mapped point of interest near a route, and how far off it lies.

    What the map draws, not what is there. A spring on the map is a spring
    somebody mapped: it may be dry, fenced, on private land, or gone.
    """

    ref: FeatureRef = Field(description="Identity of the point of interest.")
    title: str = Field(description="The feature's name in the requested language.")
    description: str | None = Field(
        description="The mapper's own note about the feature, when there is one "
        "— undated, and often the only place a condition or an access problem "
        "is recorded."
    )
    category: PoiCategory = Field(
        description="The map's own category for this feature. Assigned upstream "
        "from the feature's tags, not by this server."
    )
    subtype: str | None = Field(
        description="What kind of feature within its category, in the map "
        "site's own words — 'Spring, Pond', 'Cistern', 'Cave'. Read from the "
        "icon the map draws, so it is null where that icon has no published "
        "label."
    )
    coordinates: Coordinates = Field(
        description="Where the map places the feature's marker. A feature that "
        "is really a line or an area — a stream, a nature reserve — is marked "
        "at one point of it, so this is not the nearest part of it to the route."
    )
    distanceFromRouteMeters: int = Field(
        description="Straight-line distance from the route's drawn line to this "
        "marker, in whole metres. Not a walking distance, and it says nothing "
        "about whether anything leads there or whether the ground between is "
        "passable."
    )
    evidence: Evidence = Field(
        description="What this result rests on, and what it cannot establish."
    )
    caution: str | None = Field(
        description="A safety note that belongs with this feature wherever it is "
        "repeated. Not optional context — relay it whenever you relay the point."
    )
    ihmUrl: str = Field(description="Human-viewable page for this feature on the map site.")


#: One GeoJSON position: longitude first, then latitude. The opposite order to
#: `Coordinates`, and to how anybody says it out loud — but it is what GeoJSON
#: specifies, and anything consuming this geometry as GeoJSON expects it.
Position = tuple[Longitude, Latitude]


class LineString(Model):
    """A route drawn as one continuous line, as GeoJSON."""

    type: Literal["LineString"] = "LineString"
    coordinates: list[Position] = Field(
        description="The line's positions in order, each `[longitude, latitude]` "
        "— longitude first, per GeoJSON."
    )


class MultiLineString(Model):
    """A route drawn as several separate lines, as GeoJSON.

    Shared routes can hold more than one line: somebody saved a walk in and a
    walk out, or two days of the same trip, in one share. The lines are not
    joined, because nothing says they connect on the ground.
    """

    type: Literal["MultiLineString"] = "MultiLineString"
    coordinates: list[list[Position]] = Field(
        description="Each line's positions in order, each `[longitude, latitude]`."
    )


#: The geometry of a route: one line, or several. Discriminated on `type`, so a
#: caller reads it as ordinary GeoJSON.
RouteGeometry = Annotated[LineString | MultiLineString, Field(discriminator="type")]


class GeometryDetail(Model):
    """What was done to a route's shape to make it something to hand over."""

    pointCount: int = Field(description="Positions in the geometry as returned.")
    recordedPointCount: int = Field(
        description="Positions the map data actually holds for this route."
    )
    simplified: bool = Field(
        description="Whether positions were dropped to keep the geometry a "
        "usable size. The line still starts and ends where the recorded one does."
    )
    toleranceMeters: float | None = Field(
        description="How far the returned line may deviate from the recorded "
        "one, in metres. Null when nothing was dropped."
    )


class ResolvedRoute(Model):
    """One route's shape and what the map data records about it.

    Everything here is what a mapper or a hiker wrote down, at a date usually
    not recorded. Nothing in it establishes that the route is open, marked,
    passable, permitted, or safe today.
    """

    ref: FeatureRef = Field(description="Identity of the route that was resolved.")
    title: str = Field(description="The route's name, as its source records it.")
    description: str | None = Field(
        description="The author's own note about the route, when there is one. "
        "Often the only place a closure, a hazard or an access problem is "
        "recorded — and undated, so it may describe conditions from years ago."
    )
    activity: str | None = Field(
        description="What the route was recorded for, in upstream's own words "
        "— 'Hiking', 'Biking', '4x4'. Null when the data does not say."
    )
    difficulty: Difficulty | None = Field(
        description="Difficulty as rated by whoever recorded the route. Null "
        "when unrated, which says nothing about how hard the route is. Even "
        "when present it is one person's judgement, against no stated standard."
    )
    lengthKm: float | None = Field(
        description="Length the source records for the route, in kilometres, "
        "rounded to 10 m. Null when the data carries no length."
    )
    ascentMeters: int | None = Field(
        description="Total climb the source records, in metres. Null when the "
        "data carries none. Derived from elevation data, not surveyed."
    )
    descentMeters: int | None = Field(
        description="Total descent the source records, in metres, as a positive "
        "number. Null when the data carries none."
    )
    startPoint: Coordinates = Field(
        description="Where the route's line begins. Not a verified trailhead, "
        "parking place, or public access point."
    )
    geometry: RouteGeometry = Field(
        description="The route's shape as GeoJSON, in `[longitude, latitude]` "
        "positions. This is the line somebody drew or recorded, not a "
        "guaranteed path on the ground."
    )
    ihmUrl: str = Field(description="Human-viewable page for this route on the map site.")


class PathEnd(Model):
    """Where a calculated path really begins or ends, against what was asked for.

    A router can only start on a way it knows about, so a path starts at the
    nearest one to the point it was given. In open country that can be a long
    way off, and covering the gap is a walk the path does not include — which is
    why the requested point, the point on the path and the distance between them
    are all reported rather than only the last of the three.
    """

    requested: Coordinates = Field(description="The point this end was asked for.")
    onPath: Coordinates = Field(
        description="Where the calculated path actually starts or ends — the "
        "nearest point on a way the router knows."
    )
    metersApart: int = Field(
        description="Straight-line distance between the two, in whole metres. "
        "Ground the path does not cover; nothing establishes that it can be "
        "crossed at all."
    )
