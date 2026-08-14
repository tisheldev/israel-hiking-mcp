"""`search_hiking_routes` — what hiking routes are mapped near a point.

The map publishes no route search, so this tool builds one: cover the circle
with tiles, decode the markers in them, and then decide. Everything after the
fetch is deterministic — same centre, same radius, same constraints, same list
in the same order — because a tool an agent cannot get a stable answer out of is
a tool nobody can evaluate.

Two things it deliberately does not do. It does not widen the search when
nothing matches; an empty list with a warning is an answer, and a silently
larger area is not what was asked. And it does not rank by quality: nothing in
the tile data says a route is good.
"""

from __future__ import annotations

import logging
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from ihm_mcp.app import ToolContext, app_context, tool
from ihm_mcp.errors import InvalidInputError, tool_errors
from ihm_mcp.models import (
    IHM_ATTRIBUTION,
    Attribution,
    Coordinates,
    Difficulty,
    Language,
    Model,
    RouteSummary,
    SearchedArea,
)
from ihm_mcp.route_markers import (
    RouteConstraints,
    is_hiking_marker,
    nearest_first,
    route_summary,
    within_radius,
)
from ihm_mcp.tiles import points_in_radius

logger = logging.getLogger(__name__)

DEFAULT_RADIUS_KM = 10.0
#: Above this a search costs more tiles than the default budget allows; the tile
#: engine refuses larger areas anyway, and refusing here says why in the schema.
MAX_RADIUS_KM = 40.0
MIN_RADIUS_KM = 1.0

#: Longer than any mapped route in the country by a wide margin.
MAX_LENGTH_KM = 1000.0

DEFAULT_LIMIT = 10
MAX_LIMIT = 20


class RouteSearchResult(Model):
    """Hiking routes near one point, plus how the search was interpreted."""

    searchedArea: SearchedArea = Field(description="The circle actually searched.")
    routes: list[RouteSummary] = Field(
        description="Matching routes, nearest start point first. Empty when "
        "nothing in the area matched — which is an answer, not a failure."
    )
    warnings: list[str] = Field(
        description="Things that shaped this result — constraints that dropped "
        "routes, truncation, ratings that upstream does not carry. Worth "
        "relaying to the user."
    )
    attribution: Attribution = Field(description="Required credit for this data.")


def check_length_range(min_length_km: float | None, max_length_km: float | None) -> None:
    """Refuse a range no route could satisfy, rather than answering nothing.

    The schema bounds each end on its own; only here can the pair be seen to
    contradict itself, and an empty list would look like an answer.
    """
    if (
        min_length_km is not None
        and max_length_km is not None
        and min_length_km > max_length_km
    ):
        raise InvalidInputError(
            f"`minLengthKm` ({min_length_km:g}) is greater than `maxLengthKm` "
            f"({max_length_km:g}), so no route could match."
        )


def warnings_for(
    *,
    radius_km: float,
    in_area: list[RouteSummary],
    matched: list[RouteSummary],
    shown: list[RouteSummary],
    unnamed_markers: int,
    constraints: RouteConstraints,
) -> list[str]:
    """Everything that happened between the tiles and this result.

    The three lists narrow into each other — every route in the area, those the
    constraints kept, those the limit left — so each warning is the difference
    between one stage and the next.
    """
    warnings: list[str] = []
    if not in_area:
        warnings.append(
            f"No hiking routes are mapped within {radius_km:g} km of this point. "
            "Try a larger radius, or a centre closer to a hiking area."
        )
    elif not matched:
        warnings.append(
            f"{len(in_area)} hiking route(s) are mapped in this area, but none "
            "match the length or difficulty asked for. Relax those to see them."
        )
    if len(shown) < len(matched):
        warnings.append(
            f"Showing the {len(shown)} nearest of {len(matched)} matching routes. "
            "Narrow the radius or the length range, or raise `limit`."
        )

    unrated = sum(route.difficulty is None for route in shown)
    if constraints.difficulty and unrated:
        warnings.append(
            f"{unrated} of the routes shown carry no difficulty rating in the map "
            "data. They are listed because most routes have none, not because "
            f"they are known to be {constraints.difficulty}."
        )
    if unnamed_markers:
        warnings.append(
            f"{unnamed_markers} hiking route marker(s) in this area have no name "
            "in the map data and were left out."
        )
    return warnings


@tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
@tool_errors
async def search_hiking_routes(
    ctx: ToolContext,
    center: Annotated[
        Coordinates,
        Field(
            description="Point to search around. Use `search_places` to turn a "
            "place name into one."
        ),
    ],
    radiusKm: Annotated[
        float,
        Field(
            description="How far from `center` to look, in kilometres. Measured "
            "to each route's start marker, in a straight line.",
            ge=MIN_RADIUS_KM,
            le=MAX_RADIUS_KM,
        ),
    ] = DEFAULT_RADIUS_KM,
    minLengthKm: Annotated[
        float | None,
        Field(description="Only routes at least this long.", ge=0, le=MAX_LENGTH_KM),
    ] = None,
    maxLengthKm: Annotated[
        float | None,
        Field(description="Only routes at most this long.", ge=0, le=MAX_LENGTH_KM),
    ] = None,
    difficulty: Annotated[
        Difficulty | None,
        Field(
            description="Only routes rated this hard. Most routes carry no "
            "rating and are kept regardless — see `warnings`."
        ),
    ] = None,
    language: Annotated[
        Language,
        Field(description="Language to return names in: 'he' Hebrew, 'en' English."),
    ] = "en",
    limit: Annotated[
        int,
        Field(description="Maximum number of routes to return.", ge=1, le=MAX_LIMIT),
    ] = DEFAULT_LIMIT,
) -> RouteSearchResult:
    """Find hiking routes mapped near a point on the Israel Hiking Map.

    Returns routes whose start marker falls within `radiusKm` of `center`,
    nearest first, with their length, any difficulty rating, and a link to the
    map site. Use `search_places` first to turn a place name into coordinates.

    Only hiking routes are returned. The same map data also holds cycling and
    4x4 routes, which this tool does not search.

    What a result does not tell you: whether the route is open, marked,
    passable, permitted, safe, or has water, parking, or public access. Lengths
    and difficulty ratings are what a volunteer recorded, at an unknown date;
    most routes have no rating at all, and one that does was rated by somebody
    whose standards are not stated. `description` is often the only place a
    closure or hazard appears, and it is undated too. Treat every result as a
    lead to check, not a plan.
    """
    app = app_context(ctx)
    check_length_range(minLengthKm, maxLengthKm)
    constraints = RouteConstraints(
        minLengthKm=minLengthKm, maxLengthKm=maxLengthKm, difficulty=difficulty
    )

    points = await points_in_radius(
        app.ihm, center, radiusKm, max_tiles=app.settings.max_tiles_per_tool_call
    )

    # The tiles overshoot the circle, so the radius is applied here, to the
    # distance the result reports — not to the coarser one that picked tiles.
    nearby = within_radius(points, center, radiusKm)
    hiking_markers = [point for point in nearby if is_hiking_marker(point)]
    summaries = (
        route_summary(
            marker, center=center, base_url=str(app.settings.base_url), language=language
        )
        for marker in hiking_markers
    )
    in_area = [route for route in summaries if route is not None]
    matched = [route for route in in_area if constraints.keeps(route)]
    shown = nearest_first(matched)[:limit]

    logger.debug(
        "route search at %s: %d tile points, %d hiking routes in area, %d matched",
        center,
        len(points),
        len(in_area),
        len(matched),
    )
    return RouteSearchResult(
        searchedArea=SearchedArea(center=center, radiusKm=radiusKm),
        routes=shown,
        warnings=warnings_for(
            radius_km=radiusKm,
            in_area=in_area,
            matched=matched,
            shown=shown,
            unnamed_markers=len(hiking_markers) - len(in_area),
            constraints=constraints,
        ),
        attribution=IHM_ATTRIBUTION,
    )
