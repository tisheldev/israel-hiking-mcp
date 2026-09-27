"""`route_between_points` — how the map's own router would get from here to there.

The one tool here that answers with something nobody recorded. Every other
result in this server is a fact somebody put on a map; this one is a
computation, made now, over ways that were mapped for other reasons. That
difference is the whole design of the module.

**The path is calculated, and every response says so.** A router joins ways
that a graph says connect. It has not been walked, and nothing in it knows
about a gate, a firing zone, a stream in flood or a fence built last year. The
warning that says this leads every answer, and is not conditional on anything.

**Upstream falls back silently, so this server never passes a word through.**
`type=` takes `Hike`, `Bike` or `4WD`, and anything else — a typo, a renamed
constant — is quietly routed as walking. So the caller's `activity` is one of
three known words, mapped here to upstream's, and the profile upstream says it
used is checked against the one asked for. A cycling request that comes back as
a walking route is reported as a changed contract rather than returned.

**A path starts where the router can, not where it was asked to.** The ends are
snapped to the nearest mapped way, and getting to that way is a walk this path
does not include — so both ends report the requested point, the point actually
reached, and the distance between them.

`type=None` is not offered. The plan for this PR listed it, and upstream's own
`RoutingType` defines it, but a live call answers HTTP 500 (checked twice,
2026-08-15). The map site never sends it either: its frontend interpolates a
straight line client-side instead. A straight line between two points is not
something worth a request to a volunteer-run service, and calling one a route
would be the most misleading thing this server could return.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ihm_mcp.app import ToolContext, app_context, tool
from ihm_mcp.errors import (
    InvalidInputError,
    RouteNotFoundError,
    UpstreamNotFound,
    UpstreamSchemaChangedError,
    tool_errors,
)
from ihm_mcp.models import (
    IHM_ATTRIBUTION,
    ISRAEL_BBOX,
    Activity,
    Attribution,
    Coordinates,
    GeometryDetail,
    LineString,
    Model,
    PathEnd,
    Position,
)
from ihm_mcp.spatial import (
    METRES_PER_KM,
    fit_geometry,
    haversine_km,
    line_length_km,
    positions,
    reported_km,
)
from ihm_mcp.ui import TRAIL_MAP_TOOL_META

logger = logging.getLogger(__name__)

#: The caller's word for what they are travelling as, to upstream's `type=`.
#: Nothing else is ever sent: upstream answers an unrecognised type with a
#: walking route rather than an error, so a value passed straight through would
#: come back looking like a successful answer to a different question.
UPSTREAM_TYPES: dict[Activity, str] = {
    "Hiking": "Hike",
    "Bicycle": "Bike",
    "4x4": "4WD",
}

#: What upstream calls the profile it actually used, in the `Name` it writes on
#: the returned feature. Observed live on 2026-08-15; these are its internal
#: profile names rather than the words it takes as input, which is why they are
#: a second table.
UPSTREAM_PROFILES: dict[Activity, str] = {
    "Hiking": "Foot",
    "Bicycle": "Bike",
    "4x4": "Car4WheelDrive",
}

KNOWN_PROFILES = frozenset(UPSTREAM_PROFILES.values())

#: Both points must fall inside the area the map covers, because the router's
#: graph stops where the map does. Asking for a path outside it costs a request
#: that upstream answers by not answering at all — a live call from Haifa into
#: the sea was still open after 40 seconds.
COVERAGE = ISRAEL_BBOX

#: The furthest apart two points may be, straight line, kilometres. This is a
#: tool for planning a walk or a ride; a calculated line across the country is
#: a wall of positions nobody is going to follow turn by turn.
MAX_DISTANCE_KM = 100.0

#: Below this the two points are the same place to a router, and a "route"
#: between them is one position repeated.
MIN_DISTANCE_M = 10.0

#: How far an end may be snapped before the answer says so. Twenty metres is a
#: road's width and not worth a sentence; two hundred is a walk across a field.
SNAP_NOTICE_METERS = 100

#: The sentence that leads every answer this tool gives. Unconditional, because
#: the thing it warns about is what the tool *is* — not something that went
#: wrong with a particular call.
CALCULATED_PATH = (
    "This is a path a routing engine calculated over mapped ways just now, not "
    "a route anybody has walked or checked. Nothing establishes that it is "
    "passable, permitted, safe or sensible, and it may cross a gate, a fence, a "
    "firing zone or a stream that is not in the data. Check it against the map "
    "before anybody follows it."
)

#: What this tool cannot answer about any path it returns. Fixed, and returned
#: with every response: a calculated line looks authoritative in a way a mapped
#: one does not, so the silence needs filling in explicitly.
ROUTING_UNKNOWNS = [
    (
        "Whether the way is open, permitted or passable. Closures, gates, "
        "private land, live-fire zones, nature-reserve rules and seasonal "
        "flooding are not in the data this path was calculated from."
    ),
    (
        "How long it takes. No duration is returned, and the length here is of "
        "the drawn line — it says nothing about climb, surface or terrain."
    ),
    (
        "Whether it is safe. A calculated path can follow a road with no verge, "
        "descend ground that needs hands, or cross a stream with no bridge."
    ),
    (
        "Whether there is water, shade or shelter on it, and whether anything "
        "on the way is reachable. Use `find_pois_along_route` on a mapped route "
        "for what the map draws nearby — and read its cautions."
    ),
]


class CalculatedRoute(Model):
    """A path between two points, computed rather than recorded."""

    activity: Activity = Field(description="What the path was calculated for.")
    start: PathEnd = Field(description="Where it begins, against what was asked for.")
    end: PathEnd = Field(description="Where it ends, against what was asked for.")
    lengthKm: float = Field(
        description="Length of the calculated line in kilometres, rounded to "
        "10 m, measured before any thinning. Ground distance, not walking time, "
        "and it does not include the gaps at either end."
    )
    straightLineKm: float = Field(
        description="Distance between the two requested points as the crow "
        "flies, rounded to 10 m. Next to `lengthKm` it says how far round the "
        "mapped ways go."
    )
    geometry: LineString = Field(
        description="The path as GeoJSON, in `[longitude, latitude]` positions "
        "— longitude first. Upstream also sends an elevation for each position; "
        "it is dropped, because it is interpolated from a sampled profile and "
        "nothing here would be honest to compute from it."
    )
    geometryDetail: GeometryDetail = Field(
        description="How the returned shape relates to the calculated one."
    )
    unknowns: list[str] = Field(
        description="Questions this answer cannot settle. Not warnings about "
        "this particular path — limits of what a routing engine knows, which "
        "stay true however clean the line looks."
    )
    warnings: list[str] = Field(
        description="Things that shaped this result. The first is always that "
        "the path is calculated rather than verified. Worth relaying to the user."
    )
    attribution: Attribution = Field(description="Required credit for this data.")


# --- the shape the routing endpoint answers in -------------------------------


class Upstream(BaseModel):
    """Base for the upstream JSON models: unknown fields are somebody else's."""

    model_config = ConfigDict(extra="ignore")


class UpstreamGeometry(Upstream):
    type: str
    #: `[lng, lat]` or `[lng, lat, elevation]` — upstream sends the third
    #: ordinate whenever its elevation service answered.
    coordinates: list[list[float]]


class UpstreamFeature(Upstream):
    geometry: UpstreamGeometry | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class UpstreamRouting(Upstream):
    """The `/api/routing` FeatureCollection, as observed live on 2026-08-15.

    One feature holding a LineString, with `Name` and `Creator` properties. The
    endpoint has no published schema, so everything is optional and a shape
    this does not recognise is reported rather than read.
    """

    features: list[UpstreamFeature] = Field(default_factory=list)


def parse(payload: Any, *, host: str) -> UpstreamRouting:
    try:
        return UpstreamRouting.model_validate(payload)
    except ValidationError as exc:
        logger.warning("unrecognised routing response from %s: %s", host, exc)
        raise UpstreamSchemaChangedError(
            f"{host} answered the routing request in a shape this server does "
            "not recognise, so the path was discarded rather than guessed at."
        ) from None


def path_of(routing: UpstreamRouting, *, host: str) -> list[Position]:
    """The calculated line as GeoJSON positions, elevation dropped.

    Rounding and de-duplication are `spatial.positions`', the same as every
    other geometry this server returns, so a path and a recorded route are
    stated to the same precision.
    """
    feature = next((one for one in routing.features if one.geometry is not None), None)
    if feature is None or feature.geometry is None:
        # Upstream returns no route by returning no feature; it has also been
        # seen to simply never answer, which the client's timeout catches.
        raise RouteNotFoundError(
            "The map's router found no path between these points. They may be "
            "on either side of ground it has no ways through, or one of them "
            "may be somewhere nothing mapped reaches."
        )
    if feature.geometry.type != "LineString":
        raise UpstreamSchemaChangedError(
            f"{host} answered the routing request with a "
            f"{feature.geometry.type} where a LineString was expected."
        )
    if any(len(position) < 2 for position in feature.geometry.coordinates):
        raise UpstreamSchemaChangedError(
            f"{host} returned a path position that is not a coordinate pair."
        )
    return positions(
        Coordinates(lat=position[1], lng=position[0])
        for position in feature.geometry.coordinates
    )


def routed_profile(routing: UpstreamRouting) -> str | None:
    """The profile upstream says it used, where its `Name` still spells one.

    Upstream writes "… profile type: Foot" on the feature it returns. Only a
    word this server knows counts: an unrecognised one means the sentence was
    reworded, which is not evidence that the wrong profile ran.
    """
    for feature in routing.features:
        name = str(feature.properties.get("Name", ""))
        for profile in KNOWN_PROFILES:
            if name.endswith(f": {profile}"):
                return profile
    return None


def check_profile(routing: UpstreamRouting, activity: Activity, *, host: str) -> None:
    """Refuse a path calculated for something other than what was asked.

    Upstream maps an unrecognised `type=` to walking without saying so, so the
    failure this guards against is silent: every cycling request coming back as
    a footpath, correct-looking and wrong. Reported as a changed contract,
    because that is what a renamed type would be.
    """
    expected = UPSTREAM_PROFILES[activity]
    routed = routed_profile(routing)
    if routed is not None and routed != expected:
        logger.warning("asked %s for %s, got profile %s", host, expected, routed)
        raise UpstreamSchemaChangedError(
            f"{host} was asked for a {activity} route and calculated a "
            f"{routed} one instead, so it was discarded rather than returned "
            "as if it answered the question."
        )


# --- what this server will ask for --------------------------------------------


def check_coverage(name: str, point: Coordinates) -> None:
    """Refuse a point the router has no ways near, without spending a request."""
    if not COVERAGE.contains(point):
        raise InvalidInputError(
            f"`{name}` ({point.lat:g}, {point.lng:g}) is outside the area this "
            f"map covers (about {COVERAGE.minLat:g}–{COVERAGE.maxLat:g} N, "
            f"{COVERAGE.minLng:g}–{COVERAGE.maxLng:g} E). The router has no "
            "ways there, and would not answer at all."
        )


def check_separation(start: Coordinates, end: Coordinates) -> float:
    """The straight-line distance, if it is one a path can be asked for."""
    straight_km = haversine_km(start, end)
    if straight_km * METRES_PER_KM < MIN_DISTANCE_M:
        raise InvalidInputError(
            "`start` and `end` are the same place to within "
            f"{MIN_DISTANCE_M:g} m, so there is no path to calculate."
        )
    if straight_km > MAX_DISTANCE_KM:
        raise InvalidInputError(
            f"`start` and `end` are {straight_km:,.0f} km apart in a straight "
            f"line, and this tool routes up to {MAX_DISTANCE_KM:g} km. Route "
            "the legs of a longer trip one at a time."
        )
    return straight_km


def snapped_end(requested: Coordinates, reached: Position) -> PathEnd:
    """One end of the path, and how far the router had to move to find a way."""
    on_path = Coordinates(lat=reached[1], lng=reached[0])
    return PathEnd(
        requested=requested,
        onPath=on_path,
        metersApart=round(haversine_km(requested, on_path) * METRES_PER_KM),
    )


def warnings_for(*, start: PathEnd, end: PathEnd, detail: GeometryDetail) -> list[str]:
    """Everything a caller should know about this particular path.

    The calculated-path warning is first and unconditional; a reader who stops
    after one line has read the one that matters.
    """
    warnings = [CALCULATED_PATH]
    for verb, reached in (("starts", start), ("ends", end)):
        if reached.metersApart > SNAP_NOTICE_METERS:
            warnings.append(
                f"The path {verb} {reached.metersApart:,} m from the point "
                "asked for, at the nearest way the router knows of. Covering "
                "that gap is not part of this path, and nothing here says it "
                "can be covered at all."
            )
    if detail.simplified and detail.toleranceMeters is not None:
        warnings.append(
            f"The path was thinned from {detail.recordedPointCount:,} "
            f"calculated positions to {detail.pointCount:,}, dropping detail "
            f"finer than {detail.toleranceMeters:g} m. `lengthKm` was measured "
            "before that, so it still describes the whole path."
        )
    return warnings


@tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    # A calculated path is a line, so a host that implements MCP Apps can draw
    # it — distinctly from a recorded one, with the snapped ends shown. The
    # result itself is unchanged, and the warning above still leads it.
    meta=TRAIL_MAP_TOOL_META,
)
@tool_errors
async def route_between_points(
    ctx: ToolContext,
    start: Annotated[
        Coordinates,
        Field(
            description="Where to start. Use `search_places` to turn a place "
            "name into coordinates."
        ),
    ],
    end: Annotated[Coordinates, Field(description="Where to finish.")],
    activity: Annotated[
        Activity,
        Field(
            description="What to route for: 'Hiking' on foot, 'Bicycle', or "
            "'4x4' for a vehicle on tracks. Each uses a different set of ways."
        ),
    ] = "Hiking",
) -> CalculatedRoute:
    """Calculate a path between two points using the Israel Hiking Map's router.

    Returns the line the map site's own routing engine draws between `start` and
    `end` for the chosen activity, as GeoJSON, with its length and how far each
    end had to be moved to reach a mapped way.

    **This is a calculation, not a route anybody has taken.** Every other tool
    here returns something a person recorded on a map; this one returns a line
    computed just now by joining ways in a graph. Nothing establishes that the
    path is open, permitted, passable or safe — it can run through a firing
    zone, along a road with no verge, over a fence, or down ground that needs
    hands. Relay the warning that comes with it, every time.

    Both points must be inside the area this map covers, and no more than
    100 km apart in a straight line. The path starts and ends at the nearest way
    the router knows of, which in open country can be hundreds of metres from
    the point asked for; `start.metersApart` and `end.metersApart` say how far,
    and that ground is not part of the path.

    For a route somebody actually walked and mapped, use `search_hiking_routes`
    and `get_route_details` instead. This tool is for getting between two points
    when no mapped route joins them.
    """
    app = app_context(ctx)
    check_coverage("start", start)
    check_coverage("end", end)
    straight_km = check_separation(start, end)

    try:
        payload = await app.ihm.get_json(
            "/api/routing",
            params={
                "from": f"{start.lat},{start.lng}",
                "to": f"{end.lat},{end.lng}",
                "type": UPSTREAM_TYPES[activity],
            },
        )
    except UpstreamNotFound:
        # The endpoint answers 200 with a feature or not at all; a 404 here is
        # the route moving, not a path that does not exist.
        raise UpstreamSchemaChangedError(
            f"The routing endpoint is gone from {app.ihm.host}."
        ) from None

    routing = parse(payload, host=app.ihm.host)
    check_profile(routing, activity, host=app.ihm.host)
    path = path_of(routing, host=app.ihm.host)
    if len(path) < 2:
        raise RouteNotFoundError(
            "The map's router answered with a single position rather than a "
            "path, so there is no route between these points to return."
        )

    # Measured before thinning, like every other length this server reports:
    # what is returned is a drawing of the path, and the number describes the
    # path. Simplification of one line yields one line, so the fitted geometry
    # is a LineString whatever the tolerance.
    length_km = reported_km(line_length_km(path))
    geometry, detail = fit_geometry(LineString(coordinates=path))
    assert isinstance(geometry, LineString)

    logger.debug(
        "routed %s from %s to %s: %d positions, %.2f km",
        activity,
        start,
        end,
        detail.recordedPointCount,
        length_km,
    )
    at_start, at_end = snapped_end(start, path[0]), snapped_end(end, path[-1])
    return CalculatedRoute(
        activity=activity,
        start=at_start,
        end=at_end,
        lengthKm=length_km,
        straightLineKm=reported_km(straight_km),
        geometry=geometry,
        geometryDetail=detail,
        unknowns=ROUTING_UNKNOWNS,
        warnings=warnings_for(start=at_start, end=at_end, detail=detail),
        attribution=IHM_ATTRIBUTION,
    )
