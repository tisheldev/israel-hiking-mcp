"""`find_pois_along_route` — what the map draws near a route somebody may walk.

The tool is a chain of the three pieces the PRs before it built: an adapter
turns a route ref into a line, a corridor measures against that line, and the
tile engine fetches the ground it passes through. What is new is the arithmetic
in the middle, and the care the answer needs.

Two things shape it.

**Cost follows the route's length, not the buffer.** A corridor is metres wide
and tiles are kilometres across, so the tile count is set by how far the route
goes. A local trail costs one or two tiles; the Israel National Trail's corridor
costs more than the whole budget, and is refused rather than half-answered.

**A water result is the one people act on.** Everything here is a mapped
feature with no date on it, and for most categories that is a footnote. For
water it is the whole answer: a spring on the map is a record that somebody once
saw water, in a country where most of them are seasonal. So the caution travels
on the point itself rather than only in a warning at the end of the response,
where a summary would drop it.
"""

from __future__ import annotations

import logging
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from ihm_mcp.app import ToolContext, app_context, tool
from ihm_mcp.errors import tool_errors
from ihm_mcp.models import (
    IHM_ATTRIBUTION,
    Attribution,
    FeatureRef,
    Language,
    Model,
    PoiAlongRoute,
    PoiCategory,
)
from ihm_mcp.pois import (
    POI_UNKNOWNS,
    WATER_CAUTION,
    PoiConstraints,
    in_corridor,
    is_poi_marker,
    line_features,
    nearest_first,
    poi_along_route,
    water_in,
)
from ihm_mcp.route_sources import adapter_for
from ihm_mcp.spatial import Corridor
from ihm_mcp.tiles import MIN_BUFFER_METERS, points_along_corridor

logger = logging.getLogger(__name__)

#: Wide enough to catch a spring signposted off the path, narrow enough that
#: what comes back is plausibly reachable from the route.
DEFAULT_BUFFER_METERS = 500.0
MAX_BUFFER_METERS = 2000.0

DEFAULT_LIMIT = 20
MAX_LIMIT = 50

#: What the tool searches for when the caller does not say. Everything the map
#: classifies except `Other`, which is upstream's bucket for whatever its
#: mapping did not place — villages, synagogues, artworks and anything it
#: recognised only well enough to draw. Those outnumber the rest two to one in a
#: sampled Haifa tile, so including them by default would bury the springs.
DEFAULT_CATEGORIES: tuple[PoiCategory, ...] = (
    "Water",
    "Natural",
    "Historic",
    "Viewpoint",
    "Camping",
)


class RouteScanned(Model):
    """The route the corridor was measured along, echoed back."""

    ref: FeatureRef = Field(description="Identity of the route that was resolved.")
    title: str = Field(description="The route's name, as its source records it.")
    bufferMeters: float = Field(
        description="How far either side of the route was searched, in metres."
    )
    ihmUrl: str = Field(description="Human-viewable page for this route on the map site.")


class PoisAlongRouteResult(Model):
    """Mapped points near one route, plus what the answer does not establish."""

    route: RouteScanned = Field(description="The route and corridor actually searched.")
    pois: list[PoiAlongRoute] = Field(
        description="Matching points, nearest to the route first. Empty when "
        "nothing of the requested categories is mapped in the corridor — which "
        "is an answer about the map, not about the ground."
    )
    unknowns: list[str] = Field(
        description="Questions this data cannot answer about any of these "
        "points. Not warnings about this particular result — limits of the data "
        "itself, which stay true however complete the rest of the answer looks."
    )
    warnings: list[str] = Field(
        description="Things that shaped this result — water present, points "
        "dropped, truncation. Worth relaying to the user."
    )
    attribution: Attribution = Field(description="Required credit for this data.")


def these_are(count: int, singular: str, plural: str) -> str:
    """"1 of these is a spring" rather than "1 of these are spring(s)".

    Every one of these sentences is a safety note the caller is meant to pass
    on; a reader who trips over the grammar is a reader paying attention to the
    wrong thing.
    """
    if count == 1:
        return f"{count} of these is a {singular}."
    return f"{count} of these are {plural}."


def warnings_for(
    *,
    route_note: str,
    matched: list[PoiAlongRoute],
    shown: list[PoiAlongRoute],
    unnamed: int,
    parts: int,
    constraints: PoiConstraints,
) -> list[str]:
    """Everything that happened between the tiles and this result.

    The water caution comes first and is unconditional whenever water is in the
    answer, so a reader meets it before the list of springs rather than after.
    """
    warnings: list[str] = []
    if water := water_in(shown):
        warnings.append(
            f"{these_are(len(water), 'water feature', 'water features')} "
            f"{WATER_CAUTION} Each carries that note in its `caution` field; "
            "repeat it wherever the point is repeated."
        )
    warnings.append(route_note)

    if not matched:
        warnings.append(
            f"Nothing in the categories asked for is mapped within "
            f"{constraints.bufferMeters:g} m of this route. Widen `bufferMeters`, "
            "or ask for more categories — `Other` is excluded by default."
        )
    if len(shown) < len(matched):
        warnings.append(
            f"Showing the {len(shown)} nearest of {len(matched)} points in the "
            "corridor. Narrow `bufferMeters` or `categories`, or raise `limit`."
        )
    if streams := line_features(shown):
        warnings.append(
            f"{these_are(len(streams), 'stream or river', 'streams or rivers')} "
            "The map marks a waterway at one point on a line that may run for "
            "kilometres, so that distance is to the mark, not to wherever the "
            "route meets the water."
        )
    if unnamed == 1:
        warnings.append(
            "One mapped point within the buffer has no name in the map data and "
            "was left out."
        )
    elif unnamed:
        warnings.append(
            f"{unnamed} mapped points within the buffer have no name in the map "
            "data and were left out."
        )
    if parts > 1:
        warnings.append(
            f"This route is recorded as {parts} separate lines with gaps between "
            "them. Distances are to the nearest of those lines, so a point beside "
            "a gap is measured to whichever piece happens to be closer."
        )
    return warnings


@tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
@tool_errors
async def find_pois_along_route(
    ctx: ToolContext,
    route: Annotated[
        FeatureRef,
        Field(
            description="Which route to scan along: the `ref` object from a "
            "`search_hiking_routes` result, e.g. "
            '{"source": "OSM", "identifier": "relation_13207704"}. Only sources '
            "`get_route_details` can resolve work here."
        ),
    ],
    bufferMeters: Annotated[
        float,
        Field(
            description="How far either side of the route to look, in metres. "
            "Straight-line distance to the route's drawn line.",
            ge=MIN_BUFFER_METERS,
            le=MAX_BUFFER_METERS,
        ),
    ] = DEFAULT_BUFFER_METERS,
    categories: Annotated[
        list[PoiCategory] | None,
        Field(
            description="Which kinds of point to return. Defaults to Water, "
            "Natural, Historic, Viewpoint and Camping. `Other` is upstream's "
            "bucket for villages, places of worship, artworks and anything else "
            "it did not classify, and is included only if asked for."
        ),
    ] = None,
    language: Annotated[
        Language,
        Field(description="Language to return names in: 'he' Hebrew, 'en' English."),
    ] = "en",
    limit: Annotated[
        int,
        Field(description="Maximum number of points to return.", ge=1, le=MAX_LIMIT),
    ] = DEFAULT_LIMIT,
) -> PoisAlongRouteResult:
    """Find springs, caves, viewpoints and other mapped points near a hiking route.

    Takes the same `{source, identifier}` ref as `get_route_details`, resolves
    the route's line, and returns the points of interest the Israel Hiking Map
    draws within `bufferMeters` of it — nearest first, each with its distance
    from the route in metres and a link to the map site.

    **Water results are the reason to be careful with this tool.** A spring,
    cistern, waterhole or pool here is a feature somebody once recorded on a
    map. Nothing establishes that it is flowing now, that it can be reached,
    that entering is permitted, or that the water is safe to drink; most water
    in this country is seasonal and much of it is dry by summer. Every water
    result carries that in its `caution` field — relay it every time you relay
    the point, and never let a result here be the reason somebody carries less
    water.

    Distances are straight lines to the route's drawn line, not walking
    distances: a spring 80 m away may be 80 m down a cliff, behind a fence, or
    inside a firing zone. Categories are the map's own classification, assigned
    upstream from OpenStreetMap tags; `subtype` is the label the map site puts
    on the icon it draws.

    Cost is set by how long the route is, not by the buffer — the corridor is
    scanned as map tiles, and a trail crossing the country needs more of them
    than one call may fetch. A route that long fails with
    `search_area_too_large`; resolve one of its sections instead.

    An empty list means nothing of that kind is **mapped** near this route. It
    is not evidence that there is no water, and not evidence that there is
    nothing there.
    """
    app = app_context(ctx)
    adapter = adapter_for(route.source, app)
    resolved = await adapter.resolve(route, language)

    # Measured against the recorded line, unthinned: this tool returns numbers
    # about the route rather than the route itself, so nothing is gained by
    # simplifying it first and up to 100 m of accuracy would be lost.
    corridor = Corridor(resolved.geometry)
    constraints = PoiConstraints(
        categories=frozenset(categories or DEFAULT_CATEGORIES), bufferMeters=bufferMeters
    )

    points = await points_along_corridor(
        app.ihm,
        corridor,
        bufferMeters,
        max_tiles=app.settings.max_tiles_per_tool_call,
    )
    markers = [point for point in points if is_poi_marker(point)]
    # The tiles reach kilometres past the corridor, so the buffer is applied
    # here, to the distance the result reports — not to the coarser reckoning
    # that picked the tiles.
    near = in_corridor(markers, corridor, constraints)
    found = [
        poi
        for point, metres in near
        if (
            poi := poi_along_route(
                point,
                metres,
                base_url=str(app.settings.base_url),
                language=language,
            )
        )
        is not None
    ]
    shown = nearest_first(found)[:limit]

    logger.debug(
        "pois along %s/%s: %d tile points, %d poi markers, %d within %gm",
        route.source,
        route.identifier,
        len(points),
        len(markers),
        len(near),
        bufferMeters,
    )
    return PoisAlongRouteResult(
        route=RouteScanned(
            ref=resolved.ref,
            title=resolved.title,
            bufferMeters=bufferMeters,
            ihmUrl=resolved.ihmUrl,
        ),
        pois=shown,
        unknowns=POI_UNKNOWNS,
        warnings=warnings_for(
            route_note=adapter.note,
            matched=found,
            shown=shown,
            unnamed=len(near) - len(found),
            parts=len(corridor.line.geoms),
            constraints=constraints,
        ),
        attribution=IHM_ATTRIBUTION,
    )
