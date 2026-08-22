"""`find_pois_along_route`, from a route's line to the points beside it.

Two kinds of test live here.

The distances are checked against **hand-computed** ones: the fixture route runs
due east at a constant latitude, so a marker placed `n` metres north of it is
exactly `n` metres from the route, and the tool has to say so. The tolerance is
a few metres because a tile quantises position to a 4,096-step grid — about
2.4 m at zoom 12 — not because the arithmetic is fuzzy.

The rest goes through a real client session, because the categories, the
ordering and above all the water caution are the contract an agent actually
meets. Every tile is built in-test with `mapbox-vector-tile`'s encoder, and the
share document is written in the shape a live `/api/urls/{id}` answers with:
none of the map's own data is committed, since it is CC BY-NC-SA.
"""

from collections.abc import Iterator
from typing import Any, cast

import httpx
import mapbox_vector_tile
import mercantile
import pytest
import respx

from ihm_mcp.config import get_settings
from ihm_mcp.errors import SearchAreaTooLargeError
from ihm_mcp.models import Coordinates, FeatureRef, LineString
from ihm_mcp.pois import SUBTYPES, WATER_CAUTION, subtype_of
from ihm_mcp.spatial import Corridor, metres_per_degree
from ihm_mcp.tiles import (
    Tile,
    corridor_tiles,
    covering_tiles,
    tile_path,
    tiles_for_corridor,
)
from ihm_mcp.tiles.decode import TilePoint
from tests.conftest import connected_session

BASE_URL = str(get_settings().base_url).rstrip("/")
TILE_ROOT = "/vector/data/global_points/"

SHARE_ID = "iXKu2M4BV5"
ROUTE = {"source": "Users", "identifier": SHARE_ID}

HAIFA = Coordinates(lat=32.8191, lng=34.9984)
HAIFA_TILE = mercantile.Tile(x=2446, y=1652, z=12)


# --- placing things exactly --------------------------------------------------


def north_of(point: Coordinates, metres: float) -> Coordinates:
    """A point `metres` due north. Exact in the frame the corridor measures in,
    which is what makes the expected distances below hand-computable."""
    return Coordinates(
        lat=point.lat + metres / metres_per_degree(point.lat)[1], lng=point.lng
    )


def east_of(point: Coordinates, metres: float) -> Coordinates:
    return Coordinates(
        lat=point.lat, lng=point.lng + metres / metres_per_degree(point.lat)[0]
    )


#: The fixture route: 1.5 km due east, so every latitude on it is the same and
#: "n metres north of the line" means exactly n metres from the route.
TRACK = [east_of(HAIFA, along) for along in (0, 500, 1000, 1500)]
MIDDLE = east_of(HAIFA, 750)


# --- building the upstream answers -------------------------------------------


def share(track: list[Coordinates] = TRACK, **overrides: Any) -> dict[str, Any]:
    """One shared route, in the shape `/api/urls/{id}` really answers with."""
    return {
        "id": SHARE_ID,
        "title": "Nahal Galim",
        "description": "A circular route",
        "type": "Hiking",
        "length": 1500.0,
        "start": {"lat": track[0].lat, "lng": track[0].lng},
        "dataContainer": {
            "routes": [
                {
                    "name": "Route",
                    "segments": [
                        {
                            "latlngs": [
                                {"lat": point.lat, "lng": point.lng, "alt": 120.0}
                                for point in track
                            ]
                        }
                    ],
                }
            ]
        },
        **overrides,
    }


def pixels(tile: Tile, point: Coordinates, *, extent: int = 4096) -> tuple[int, int]:
    left, bottom, right, top = mercantile.xy_bounds(tile)
    x, y = mercantile.xy(point.lng, point.lat)
    return (
        round((x - left) / (right - left) * extent),
        round((top - y) / (top - bottom) * extent),
    )


def marker(
    feature_id: int,
    at: Coordinates,
    *,
    category: str,
    icon: str = "",
    tile: Tile = HAIFA_TILE,
    **properties: Any,
) -> dict[str, Any]:
    return {
        "geometry": {"type": "Point", "coordinates": list(pixels(tile, at))},
        "properties": {
            "poiCategory": category,
            "poiIcon": icon,
            "poiSource": "OSM",
            "poiLength": 0.0,
            **properties,
        },
        "id": feature_id,
    }


def tile_bytes(features: list[dict[str, Any]], *, layer: str = "global_points") -> bytes:
    return cast(
        bytes,
        mapbox_vector_tile.encode(
            [{"name": layer, "features": features}],
            default_options={"y_coord_down": True, "extents": 4096},
        ),
    )


# A spread of categories at known offsets from the line. The distances in the
# assertions below are these numbers, not anything read off a run.
SPRING = marker(
    31, north_of(MIDDLE, 120), category="Water", icon="icon-tint", name="Ein Ovadia"
)
CAVE = marker(
    41,
    north_of(MIDDLE, 300),
    category="Natural",
    icon="icon-cave",
    name="Double Cave",
    description="A cave with two mouths",
)
WELL = marker(
    51,
    north_of(MIDDLE, -480),
    category="Water",
    icon="icon-water-well",
    name="Vardiya Well",
)
STREAM = marker(
    61, north_of(MIDDLE, 400), category="Water", icon="icon-river", name="Nahal Gdora"
)
FAR_VIEWPOINT = marker(
    71,
    north_of(MIDDLE, 1400),
    category="Viewpoint",
    icon="icon-viewpoint",
    name="Carmel Lookout",
)
SQUARE = marker(
    81, north_of(MIDDLE, 60), category="Other", icon="icon-home", name="Paris Square"
)
UNNAMED_POOL = marker(91, north_of(MIDDLE, 200), category="Water", icon="icon-tint")
#: A route marker, in the same tiles and the same shape — this tool's job is to
#: leave it to `search_hiking_routes`.
ROUTE_MARKER = marker(
    101,
    north_of(MIDDLE, 90),
    category="Hiking",
    icon="icon-hike",
    poiLength=8000.0,
    name="Haifa Trail",
)

ALL_MARKERS = [
    SPRING,
    CAVE,
    WELL,
    STREAM,
    FAR_VIEWPOINT,
    SQUARE,
    UNNAMED_POOL,
    ROUTE_MARKER,
]


@pytest.fixture
def mock_upstream() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


def serve(
    router: respx.MockRouter,
    *,
    tiles: dict[Tile, bytes],
    document: dict[str, Any] | None = None,
) -> respx.MockRouter:
    """The share behind the route ref, and each named tile; empty ground else."""
    router.get(path=f"/api/urls/{SHARE_ID}").mock(
        return_value=httpx.Response(200, json=document if document is not None else share())
    )

    def respond(request: httpx.Request) -> httpx.Response:
        for tile, blob in tiles.items():
            if request.url.path == tile_path(tile):
                return httpx.Response(
                    200, content=blob, headers={"content-type": "application/x-protobuf"}
                )
        return httpx.Response(404)

    router.get(path__startswith=TILE_ROOT).mock(side_effect=respond)
    return router


@pytest.fixture
def pois_near_the_route(mock_upstream: respx.MockRouter) -> respx.MockRouter:
    return serve(mock_upstream, tiles={HAIFA_TILE: tile_bytes(ALL_MARKERS)})


async def call(**arguments: Any) -> dict[str, Any]:
    async with connected_session() as session:
        result = await session.call_tool(
            "find_pois_along_route", {"route": ROUTE, **arguments}
        )

    assert result.isError is False, result.content
    assert result.structuredContent is not None
    return result.structuredContent


async def call_expecting_error(**arguments: Any) -> str:
    async with connected_session() as session:
        result = await session.call_tool(
            "find_pois_along_route", {"route": ROUTE, **arguments}
        )

    assert result.isError is True
    return "\n".join(getattr(block, "text", "") for block in result.content)


def titles(result: dict[str, Any]) -> list[str]:
    return [poi["title"] for poi in result["pois"]]


def distances(result: dict[str, Any]) -> dict[str, int]:
    return {poi["title"]: poi["distanceFromRouteMeters"] for poi in result["pois"]}


# --- the distances -----------------------------------------------------------


async def test_each_point_reports_the_distance_it_was_placed_at(
    pois_near_the_route: respx.MockRouter,
):
    """Hand-computed: the route runs at one latitude, so a marker placed 120 m
    north of it is 120 m from the route. The few metres of tolerance are the
    tile grid's quantisation, about 2.4 m at zoom 12."""
    result = await call(bufferMeters=1000, limit=50)

    assert distances(result) == {
        "Ein Ovadia": pytest.approx(120, abs=4),
        "Double Cave": pytest.approx(300, abs=4),
        "Nahal Gdora": pytest.approx(400, abs=4),
        "Vardiya Well": pytest.approx(480, abs=4),
    }


def test_the_corridor_measures_perpendicular_distance_to_the_line():
    """The unit underneath, with no tile in the way — so the numbers are exact
    rather than quantised."""
    corridor = Corridor(
        LineString(coordinates=[(point.lng, point.lat) for point in TRACK])
    )

    assert corridor.metres_to(MIDDLE) == pytest.approx(0, abs=0.01)
    assert corridor.metres_to(north_of(MIDDLE, 250)) == pytest.approx(250, abs=0.5)
    assert corridor.metres_to(north_of(MIDDLE, -250)) == pytest.approx(250, abs=0.5)
    # Off the end of the line: distance to the endpoint, not to its extension.
    beyond = east_of(TRACK[-1], 300)
    assert corridor.metres_to(beyond) == pytest.approx(300, abs=0.5)


def test_a_point_off_the_end_is_measured_to_the_nearest_end():
    corridor = Corridor(
        LineString(coordinates=[(point.lng, point.lat) for point in TRACK])
    )
    corner = north_of(east_of(TRACK[-1], 300), 400)

    # 3-4-5: 300 m past the end and 400 m to the side is 500 m from it.
    assert corridor.metres_to(corner) == pytest.approx(500, abs=1)


# --- what comes back ---------------------------------------------------------


async def test_points_come_back_nearest_to_the_route_first(
    pois_near_the_route: respx.MockRouter,
):
    result = await call(bufferMeters=1000, limit=50)

    assert titles(result) == [
        "Ein Ovadia",  # 120 m
        "Double Cave",  # 300 m
        "Nahal Gdora",  # 400 m
        "Vardiya Well",  # 480 m
    ]


async def test_a_point_carries_its_identity_classification_and_link(
    pois_near_the_route: respx.MockRouter,
):
    result = await call()
    spring = result["pois"][0]

    assert spring["ref"] == {"source": "OSM", "identifier": "node_3"}
    assert spring["title"] == "Ein Ovadia"
    assert spring["category"] == "Water"
    assert spring["subtype"] == "Spring, Pond"
    assert spring["coordinates"]["lat"] == pytest.approx(
        north_of(MIDDLE, 120).lat, abs=1e-4
    )
    assert spring["ihmUrl"] == f"{BASE_URL}/poi/OSM/node_3?language=en"
    assert "CC BY-NC-SA" in " ".join(result["attribution"]["sources"])


async def test_the_route_that_was_scanned_is_echoed_back(
    pois_near_the_route: respx.MockRouter,
):
    result = await call(bufferMeters=250)

    assert result["route"] == {
        "ref": ROUTE,
        "title": "Nahal Galim",
        "bufferMeters": 250,
        "ihmUrl": f"{BASE_URL}/share/{SHARE_ID}",
    }


async def test_a_mappers_note_comes_through(pois_near_the_route: respx.MockRouter):
    result = await call()
    cave = next(poi for poi in result["pois"] if poi["title"] == "Double Cave")

    assert cave["description"] == "A cave with two mouths"


async def test_every_point_says_what_it_rests_on(pois_near_the_route: respx.MockRouter):
    """The evidence model is constant, and that is the point of it: nothing here
    is an observation, and none of it carries a date."""
    result = await call(bufferMeters=1000, limit=50)

    assert result["pois"]
    for poi in result["pois"]:
        assert poi["evidence"] == {
            "kind": "mapped_feature",
            "observedAt": None,
            "freshnessKnown": False,
        }


# --- the water rule ----------------------------------------------------------


async def test_every_water_point_carries_the_caution_itself(
    pois_near_the_route: respx.MockRouter,
):
    """Not only in `warnings`: a summary that keeps the points and drops the
    warnings must still carry it."""
    result = await call(bufferMeters=1000, limit=50, categories=["Water"])

    assert titles(result) == ["Ein Ovadia", "Nahal Gdora", "Vardiya Well"]
    for poi in result["pois"]:
        assert poi["caution"] == WATER_CAUTION


async def test_a_point_that_is_not_water_carries_no_caution(
    pois_near_the_route: respx.MockRouter,
):
    result = await call(categories=["Natural"])

    assert titles(result) == ["Double Cave"]
    assert result["pois"][0]["caution"] is None


async def test_water_in_the_answer_is_warned_about_before_anything_else(
    pois_near_the_route: respx.MockRouter,
):
    result = await call(bufferMeters=1000, limit=50)

    assert WATER_CAUTION in result["warnings"][0]
    assert "3 of these are water features" in result["warnings"][0]


async def test_one_water_point_is_warned_about_in_the_singular(
    pois_near_the_route: respx.MockRouter,
):
    result = await call(bufferMeters=150)

    assert "1 of these is a water feature" in result["warnings"][0]


async def test_no_water_in_the_answer_means_no_water_warning(
    pois_near_the_route: respx.MockRouter,
):
    result = await call(categories=["Natural"])

    assert not any(WATER_CAUTION in warning for warning in result["warnings"])


async def test_the_unknowns_lead_with_water_and_are_always_returned(
    pois_near_the_route: respx.MockRouter,
):
    empty = await call(bufferMeters=25)
    full = await call(bufferMeters=1000, limit=50)

    assert empty["unknowns"] == full["unknowns"]
    assert "flowing today" in full["unknowns"][0]


async def test_a_waterway_says_its_distance_is_to_one_mark_on_a_line(
    pois_near_the_route: respx.MockRouter,
):
    result = await call(bufferMeters=1000, limit=50)

    assert "not to wherever the route meets the water" in " ".join(result["warnings"])


# --- what gets filtered out --------------------------------------------------


async def test_route_markers_are_left_to_the_route_search(
    pois_near_the_route: respx.MockRouter,
):
    """A hiking route marker sits 90 m from this line, closer than anything
    returned, and is not a point of interest."""
    result = await call(bufferMeters=1000, limit=50)

    assert "Haifa Trail" not in titles(result)


async def test_other_is_excluded_unless_it_is_asked_for(
    pois_near_the_route: respx.MockRouter,
):
    """`Other` is upstream's bucket for villages, synagogues and artworks, and
    it outnumbers everything else in a built-up area."""
    default = await call(bufferMeters=1000, limit=50)
    asked = await call(bufferMeters=1000, limit=50, categories=["Other"])

    assert "Paris Square" not in titles(default)
    assert titles(asked) == ["Paris Square"]


async def test_a_point_outside_the_buffer_is_not_returned(
    pois_near_the_route: respx.MockRouter,
):
    """The tile fetched covers ground kilometres past the corridor."""
    result = await call(bufferMeters=500)

    assert titles(result) == ["Ein Ovadia", "Double Cave", "Nahal Gdora", "Vardiya Well"]
    assert "Carmel Lookout" not in titles(result)


async def test_widening_the_buffer_reaches_the_far_point(
    pois_near_the_route: respx.MockRouter,
):
    result = await call(bufferMeters=1500, limit=50)

    assert "Carmel Lookout" in titles(result)


async def test_a_point_the_map_does_not_name_is_left_out_and_counted(
    pois_near_the_route: respx.MockRouter,
):
    result = await call(bufferMeters=250)

    assert titles(result) == ["Ein Ovadia"]
    assert "One mapped point within the buffer has no name" in " ".join(
        result["warnings"]
    )


async def test_several_unnamed_points_are_counted_in_the_plural(
    mock_upstream: respx.MockRouter,
):
    serve(
        mock_upstream,
        tiles={
            HAIFA_TILE: tile_bytes(
                [
                    UNNAMED_POOL,
                    marker(93, north_of(MIDDLE, 210), category="Water", icon="icon-tint"),
                ]
            )
        },
    )

    result = await call(bufferMeters=250)

    assert result["pois"] == []
    assert "2 mapped points within the buffer have no name" in " ".join(
        result["warnings"]
    )


# --- ordering and truncation -------------------------------------------------


async def test_the_limit_keeps_the_nearest_and_says_what_was_dropped(
    pois_near_the_route: respx.MockRouter,
):
    result = await call(bufferMeters=1000, limit=2)

    assert titles(result) == ["Ein Ovadia", "Double Cave"]
    assert "Showing the 2 nearest of 4 points" in " ".join(result["warnings"])


async def test_the_same_scan_twice_gives_the_same_answer(
    pois_near_the_route: respx.MockRouter,
):
    first = await call(bufferMeters=1000, limit=50)
    second = await call(bufferMeters=1000, limit=50)

    assert first == second


async def test_points_at_the_same_distance_are_ordered_by_their_identity(
    mock_upstream: respx.MockRouter,
):
    """Two markers can sit on the same spot, and an order that depends on which
    tile answered first is not something to evaluate against."""
    together = north_of(MIDDLE, 200)
    serve(
        mock_upstream,
        tiles={
            HAIFA_TILE: tile_bytes(
                [
                    marker(72, together, category="Water", icon="icon-tint", name="Second"),
                    marker(52, together, category="Water", icon="icon-tint", name="First"),
                ]
            )
        },
    )

    result = await call(bufferMeters=1000, limit=50)

    assert [poi["ref"]["identifier"] for poi in result["pois"]] == ["way_5", "way_7"]
    assert titles(result) == ["First", "Second"]


# --- nothing to return -------------------------------------------------------


async def test_a_corridor_with_nothing_in_it_is_an_empty_list_not_a_failure(
    mock_upstream: respx.MockRouter,
):
    serve(mock_upstream, tiles={})

    result = await call()

    assert result["pois"] == []
    assert "Nothing in the categories asked for is mapped within 500 m" in " ".join(
        result["warnings"]
    )


async def test_an_empty_result_still_says_what_it_does_not_establish(
    mock_upstream: respx.MockRouter,
):
    """"No water is mapped here" must not read as "there is no water here"."""
    serve(mock_upstream, tiles={})

    result = await call(categories=["Water"])

    assert "not that nothing is there" in " ".join(result["unknowns"])


# --- the route the corridor is drawn along -----------------------------------


async def test_a_source_this_server_cannot_resolve_is_refused_by_name():
    text = await call_expecting_error(route={"source": "Nakeb", "identifier": "255"})

    assert "[unsupported_source]" in text


async def test_a_route_that_does_not_exist_is_reported_as_missing(
    mock_upstream: respx.MockRouter,
):
    mock_upstream.get(path=f"/api/urls/{SHARE_ID}").mock(return_value=httpx.Response(404))

    text = await call_expecting_error()

    assert "[route_not_found]" in text


async def test_the_source_note_travels_with_the_answer(
    pois_near_the_route: respx.MockRouter,
):
    """Who recorded the route qualifies every distance measured against it."""
    result = await call()

    assert "one person's saved and shared route" in " ".join(result["warnings"])


async def test_a_route_recorded_in_pieces_says_so(mock_upstream: respx.MockRouter):
    """A share can hold two separate lines, and a point beside the gap between
    them is measured to whichever piece is closer."""
    second_day = [north_of(HAIFA, 3000), north_of(east_of(HAIFA, 500), 3000)]
    document = share()
    document["dataContainer"]["routes"].append(
        {
            "name": "Second day",
            "segments": [
                {
                    "latlngs": [
                        {"lat": point.lat, "lng": point.lng} for point in second_day
                    ]
                }
            ],
        }
    )
    serve(mock_upstream, tiles={HAIFA_TILE: tile_bytes(ALL_MARKERS)}, document=document)

    result = await call()

    assert "recorded as 2 separate lines" in " ".join(result["warnings"])


# --- the area a scan is allowed to cover -------------------------------------


@pytest.mark.parametrize("buffer_meters", [0, 24, 2001, -100])
async def test_a_buffer_outside_the_supported_range_is_rejected(buffer_meters: float):
    text = await call_expecting_error(bufferMeters=buffer_meters)

    assert "bufferMeters" in text


@pytest.mark.parametrize("limit", [0, 51])
async def test_a_limit_outside_the_supported_range_is_rejected(limit: int):
    text = await call_expecting_error(limit=limit)

    assert "limit" in text


def long_corridor() -> Corridor:
    """A route the length of the country, which no budget covers at zoom 12."""
    return Corridor(
        LineString(
            coordinates=[
                (point.lng, point.lat)
                for point in (
                    Coordinates(lat=33.2, lng=35.5),
                    Coordinates(lat=31.5, lng=35.0),
                    Coordinates(lat=29.6, lng=34.9),
                )
            ]
        )
    )


def test_a_long_route_costs_more_than_the_budget_at_any_buffer():
    """What a scan costs has a floor set by the route's length: at the narrowest
    corridor this tool offers, a route the length of the country already needs
    more tiles than the default budget. That is why the error for one of these
    does not suggest narrowing the buffer."""
    corridor = long_corridor()
    budget = get_settings().max_tiles_per_tool_call

    assert len(corridor_tiles(corridor, 25, 12)) > budget / 2
    assert len(corridor_tiles(corridor, 2000, 12)) > budget / 2


def test_the_corridor_is_cheaper_than_the_box_around_it():
    """A route running diagonally leaves most of its bounding box empty."""
    corridor = long_corridor()

    assert len(corridor_tiles(corridor, 500, 12)) < len(
        covering_tiles(corridor.bounds(500), 12)
    ) / 2


def test_a_route_too_long_to_scan_is_refused_and_told_the_buffer_will_not_help():
    corridor = long_corridor()

    with pytest.raises(SearchAreaTooLargeError) as raised:
        tiles_for_corridor(corridor, 500, zoom=12, max_tiles=10)

    message = str(raised.value)
    assert "[search_area_too_large]" in message
    assert "this route is simply long" in message


def test_a_buffer_that_would_have_fitted_is_suggested():
    """Where the route is short and the buffer is what blew the budget, saying
    "the route is too long" would be false."""
    corridor = Corridor(
        LineString(coordinates=[(point.lng, point.lat) for point in TRACK])
    )
    at_2km = len(corridor_tiles(corridor, 2000, 12))

    with pytest.raises(SearchAreaTooLargeError) as raised:
        tiles_for_corridor(corridor, 2000, zoom=12, max_tiles=at_2km - 1)

    assert "Try a narrower `bufferMeters`" in str(raised.value)


async def test_a_scan_past_the_tile_budget_fails_rather_than_answering_partly(
    monkeypatch: pytest.MonkeyPatch, mock_upstream: respx.MockRouter
):
    serve(mock_upstream, tiles={HAIFA_TILE: tile_bytes(ALL_MARKERS)})
    monkeypatch.setenv("IHM_MAX_TILES_PER_TOOL_CALL", "1")
    get_settings.cache_clear()
    try:
        # 2 km either side of a 1.5 km route spans more than one zoom-12 tile.
        text = await call_expecting_error(bufferMeters=2000)
    finally:
        get_settings.cache_clear()

    assert "[search_area_too_large]" in text


# --- the pieces on their own -------------------------------------------------


def point_with(**properties: Any) -> TilePoint:
    return TilePoint(
        ref=FeatureRef(source="OSM", identifier="node_1"),
        coordinates=HAIFA,
        layer="global_points",
        properties=properties,
    )


@pytest.mark.parametrize(
    ("icon", "expected"),
    [
        ("icon-tint", "Spring, Pond"),
        ("icon-waterhole", "Waterhole"),
        ("icon-water-well", "Water Well"),
        ("icon-cistern", "Cistern"),
        ("icon-waterfall", "Waterfall"),
        ("icon-river", "Stream, River"),
        ("icon-cave", "Cave"),
        ("icon-viewpoint", "Viewpoint"),
        ("icon-campsite", "Campsite"),
        ("icon-ruins", "Ruins"),
        # `icon-search` is upstream's fallback for a feature it recognised only
        # well enough to draw, so there is nothing to call it.
        ("icon-search", None),
        ("icon-invented-tomorrow", None),
        ("", None),
    ],
)
def test_the_subtype_is_the_map_site_s_own_label_for_the_icon_it_draws(
    icon: str, expected: str | None
):
    assert subtype_of(point_with(poiIcon=icon)) == expected


def test_every_water_icon_the_map_site_draws_has_a_label():
    """The five in the site's own POI category list, plus the waterway icon it
    draws but does not offer."""
    water_icons = {
        "icon-tint",
        "icon-waterfall",
        "icon-waterhole",
        "icon-water-well",
        "icon-cistern",
        "icon-river",
    }

    assert water_icons <= SUBTYPES.keys()
