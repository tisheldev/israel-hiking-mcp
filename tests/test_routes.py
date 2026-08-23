"""`search_hiking_routes`, from a tile's bytes to the tool's answer.

Every tile below is built in-test with `mapbox-vector-tile`'s encoder, for the
same reason as `test_tiles.py`: the map's data is CC BY-NC-SA and a captured
tile would be a redistribution. The property names and shapes are the ones a
live zoom-12 tile over Haifa really carried on 2026-08-14 — `poiCategory`,
`poiLength` in metres as a float, `poiDifficulty` on the rare route that has
one, and names under `name`, `name:he` and `name:en`.

Most of it goes through a real client session, because the schema, the ordering
and the warnings are the contract an agent actually meets.
"""

from collections.abc import Iterator
from typing import Any, cast

import httpx
import mapbox_vector_tile
import mercantile
import pytest
import respx

from ihm_mcp import tiles
from ihm_mcp.config import get_settings
from ihm_mcp.models import Coordinates, FeatureRef, Language, RouteSummary
from ihm_mcp.route_markers import RouteConstraints, length_km, nearest_first
from ihm_mcp.spatial import haversine_km
from ihm_mcp.tiles import Tile, tile_path, tiles_for_radius
from tests.conftest import connected_session, tags

BASE_URL = str(get_settings().base_url).rstrip("/")
TILE_ROOT = "/vector/data/global_points/"

HAIFA = Coordinates(lat=32.8191, lng=34.9984)
HAIFA_TILE = mercantile.Tile(x=2446, y=1652, z=12)


# --- building a tile of routes -----------------------------------------------


def pixels(tile: Tile, point: Coordinates, *, extent: int = 4096) -> tuple[int, int]:
    left, bottom, right, top = mercantile.xy_bounds(tile)
    x, y = mercantile.xy(point.lng, point.lat)
    return (
        round((x - left) / (right - left) * extent),
        round((top - y) / (top - bottom) * extent),
    )


def marker(
    feature_id: int, at: Coordinates, *, tile: Tile = HAIFA_TILE, **properties: Any
) -> dict[str, Any]:
    """A route marker: a point carrying the whole route in its properties."""
    return {
        "geometry": {"type": "Point", "coordinates": list(pixels(tile, at))},
        "properties": {"poiCategory": "Hiking", "poiSource": "OSM", **properties},
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


def north_of(center: Coordinates, km: float) -> Coordinates:
    """A point `km` due north — near enough for a fixture, exactly reproducible."""
    return Coordinates(lat=center.lat + km / 111.32, lng=center.lng)


# Three routes at known distances and lengths, plus one of each awkward kind.
NEARBY = north_of(HAIFA, 1.0)
MIDDLE = north_of(HAIFA, 3.0)
FURTHER = north_of(HAIFA, 6.0)

ROUTES = [
    marker(
        46473503,
        MIDDLE,
        poiLength=8000.0,
        name="Nahal Ovadia",
        **tags({"name:en": "Nahal Ovadia", "name:he": "נחל עובדיה"}),
    ),
    marker(
        34721793,
        NEARBY,
        poiLength=3504.880687667189,
        name="שביל חיפה - העיר התחתית",
        description="Segment 1: the lower city",
        **tags({"name:en": "Haifa Trail - Downtown", "name:he": "שביל חיפה - העיר התחתית"}),
    ),
    marker(
        37341163,
        FURTHER,
        poiLength=51106.61643053978,
        name="Haifa Wadis Trail",
        **tags({"name:en": "Haifa Wadis Trail"}),
    ),
]

#: `external` carries the non-OSM datasets; Nakeb routes are where a difficulty
#: rating actually turns up.
NAKEB = {
    "geometry": {"type": "Point", "coordinates": list(pixels(HAIFA_TILE, NEARBY))},
    "properties": {
        "poiCategory": "Hiking",
        "poiSource": "Nakeb",
        "identifier": "255",
        "poiDifficulty": "Moderate",
        "poiLength": 7500.0,
        "name": "טיול מדרגות חיפה - אתר נָאקֶבּ",
        "description": "טיול אורבני בחיפה",
    },
    "id": 507,
}


@pytest.fixture
def mock_upstream() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


def serve(router: respx.MockRouter, bodies: dict[Tile, bytes]) -> respx.Route:
    """Answer each named tile with its body; everything else is empty ground."""

    def respond(request: httpx.Request) -> httpx.Response:
        for tile, blob in bodies.items():
            if request.url.path == tile_path(tile):
                return httpx.Response(
                    200, content=blob, headers={"content-type": "application/x-protobuf"}
                )
        return httpx.Response(404)

    return router.get(path__startswith=TILE_ROOT).mock(side_effect=respond)


@pytest.fixture
def routes_near_haifa(mock_upstream: respx.MockRouter) -> respx.Route:
    return serve(mock_upstream, {HAIFA_TILE: tile_bytes(ROUTES)})


def around_haifa(**arguments: Any) -> dict[str, Any]:
    """Tool arguments centred on Haifa, which is where every fixture tile is."""
    return {"center": {"lat": HAIFA.lat, "lng": HAIFA.lng}, **arguments}


async def call(**arguments: Any) -> dict[str, Any]:
    async with connected_session() as session:
        result = await session.call_tool("search_hiking_routes", around_haifa(**arguments))

    assert result.isError is False, result.content
    assert result.structuredContent is not None
    return result.structuredContent


async def call_expecting_error(**arguments: Any) -> str:
    async with connected_session() as session:
        result = await session.call_tool("search_hiking_routes", around_haifa(**arguments))

    assert result.isError is True
    return "\n".join(getattr(block, "text", "") for block in result.content)


def titles(result: dict[str, Any]) -> list[str]:
    return [route["title"] for route in result["routes"]]


# --- what comes back ---------------------------------------------------------


async def test_routes_in_the_area_come_back_nearest_first(routes_near_haifa: respx.Route):
    result = await call(radiusKm=10)

    assert titles(result) == [
        "Haifa Trail - Downtown",  # 1 km
        "Nahal Ovadia",  # 3 km
        "Haifa Wadis Trail",  # 6 km
    ]


async def test_a_route_carries_its_identity_measurements_and_link(
    routes_near_haifa: respx.Route,
):
    result = await call(radiusKm=10)
    downtown = result["routes"][0]

    assert downtown["ref"] == {"source": "OSM", "identifier": "relation_3472179"}
    assert downtown["lengthKm"] == pytest.approx(3.5)
    assert downtown["distanceFromSearchCenterKm"] == pytest.approx(1.0, abs=0.02)
    assert downtown["description"] == "Segment 1: the lower city"
    assert downtown["difficulty"] is None
    assert downtown["startPoint"]["lat"] == pytest.approx(NEARBY.lat, abs=1e-4)
    assert downtown["ihmUrl"] == f"{BASE_URL}/poi/OSM/relation_3472179?language=en"
    assert "CC BY-NC-SA" in " ".join(result["attribution"]["sources"])


async def test_the_searched_area_is_echoed_back(routes_near_haifa: respx.Route):
    result = await call(radiusKm=7)

    assert result["searchedArea"] == {
        "center": {"lat": HAIFA.lat, "lng": HAIFA.lng},
        "radiusKm": 7,
    }


async def test_names_come_back_in_the_language_asked_for(routes_near_haifa: respx.Route):
    result = await call(radiusKm=10, language="he")

    assert "נחל עובדיה" in titles(result)
    assert result["routes"][0]["ihmUrl"].endswith("?language=he")


async def test_a_route_from_another_dataset_keeps_its_own_source(
    mock_upstream: respx.MockRouter,
):
    """Nakeb routes are real results even though later tools cannot resolve
    their geometry — the ref says which dataset they came from."""
    serve(mock_upstream, {HAIFA_TILE: tile_bytes([NAKEB], layer="external")})

    result = await call(radiusKm=10)
    (route,) = result["routes"]

    assert route["ref"] == {"source": "Nakeb", "identifier": "255"}
    assert route["difficulty"] == "Moderate"
    assert route["ihmUrl"] == f"{BASE_URL}/poi/Nakeb/255?language=en"


# --- what gets filtered out --------------------------------------------------


async def test_only_hiking_routes_are_returned(mock_upstream: respx.MockRouter):
    """The same tiles carry cycling and 4x4 routes and every kind of POI."""
    serve(
        mock_upstream,
        {
            HAIFA_TILE: tile_bytes(
                [
                    marker(11, NEARBY, poiCategory="Bicycle", poiLength=9000.0, name="Ride"),
                    marker(21, NEARBY, poiCategory="4x4", poiLength=9000.0, name="Track"),
                    marker(31, NEARBY, poiCategory="Water", name="Spring"),
                    marker(41, NEARBY, poiLength=9000.0, name="Walk"),
                ]
            )
        },
    )

    result = await call(radiusKm=10)

    assert titles(result) == ["Walk"]


async def test_a_route_outside_the_radius_is_not_returned(routes_near_haifa: respx.Route):
    """The tiles fetched cover more ground than the circle asked about."""
    result = await call(radiusKm=2)

    assert titles(result) == ["Haifa Trail - Downtown"]


@pytest.mark.parametrize(
    ("constraint", "expected"),
    [
        ({"minLengthKm": 5}, ["Nahal Ovadia", "Haifa Wadis Trail"]),
        ({"maxLengthKm": 5}, ["Haifa Trail - Downtown"]),
        ({"minLengthKm": 3, "maxLengthKm": 10}, ["Haifa Trail - Downtown", "Nahal Ovadia"]),
        ({"minLengthKm": 8, "maxLengthKm": 8}, ["Nahal Ovadia"]),
        ({"minLengthKm": 100}, []),
    ],
)
async def test_the_length_range_is_inclusive_of_its_bounds(
    routes_near_haifa: respx.Route, constraint: dict[str, float], expected: list[str]
):
    """A route listed as 8.0 km is one an 8 km limit keeps: the filter reads the
    same rounded number the result reports."""
    result = await call(radiusKm=10, **constraint)

    assert titles(result) == expected


async def test_a_length_range_that_no_route_could_satisfy_is_refused():
    text = await call_expecting_error(minLengthKm=12, maxLengthKm=4)

    assert "[invalid_input]" in text


async def test_a_rated_route_is_kept_only_by_its_own_rating(
    mock_upstream: respx.MockRouter,
):
    serve(
        mock_upstream,
        {
            HAIFA_TILE: tile_bytes(
                [
                    marker(11, NEARBY, poiDifficulty="Easy", poiLength=4000.0, name="Easy"),
                    marker(21, MIDDLE, poiDifficulty="Hard", poiLength=4000.0, name="Hard"),
                ]
            )
        },
    )

    result = await call(radiusKm=10, difficulty="Easy")

    assert titles(result) == ["Easy"]


async def test_an_unrated_route_passes_a_difficulty_filter_and_says_so(
    routes_near_haifa: respx.Route,
):
    """The map site's own rule: the rating reaches almost no OSM route, and
    dropping the unrated ones would empty the search."""
    result = await call(radiusKm=10, difficulty="Easy")

    assert len(result["routes"]) == 3
    assert all(route["difficulty"] is None for route in result["routes"])
    assert "carry no difficulty rating" in " ".join(result["warnings"])


async def test_an_unrated_route_is_not_mentioned_when_no_rating_was_asked_for(
    routes_near_haifa: respx.Route,
):
    result = await call(radiusKm=10)

    assert result["warnings"] == []


async def test_a_rating_upstream_does_not_publish_is_reported_as_none(
    mock_upstream: respx.MockRouter,
):
    """A grade nobody can interpret is worse than an honest null."""
    serve(
        mock_upstream,
        {
            HAIFA_TILE: tile_bytes(
                [marker(11, NEARBY, poiDifficulty="Extreme", poiLength=4000.0, name="Route")]
            )
        },
    )

    result = await call(radiusKm=10)

    assert result["routes"][0]["difficulty"] is None


async def test_a_route_the_map_does_not_name_is_left_out_and_counted(
    mock_upstream: respx.MockRouter,
):
    serve(
        mock_upstream,
        {
            HAIFA_TILE: tile_bytes(
                [
                    marker(11, NEARBY, poiLength=4000.0),
                    marker(21, MIDDLE, poiLength=4000.0, name="Named"),
                ]
            )
        },
    )

    result = await call(radiusKm=10)

    assert titles(result) == ["Named"]
    assert "1 hiking route marker(s) in this area have no name" in " ".join(
        result["warnings"]
    )


async def test_a_route_with_no_length_cannot_satisfy_a_length_range(
    mock_upstream: respx.MockRouter,
):
    serve(
        mock_upstream,
        {HAIFA_TILE: tile_bytes([marker(11, NEARBY, name="Unmeasured")])},
    )

    unfiltered = await call(radiusKm=10)
    filtered = await call(radiusKm=10, minLengthKm=1)

    assert unfiltered["routes"][0]["lengthKm"] is None
    assert filtered["routes"] == []


# --- ordering and truncation -------------------------------------------------


async def test_the_limit_keeps_the_nearest_and_says_what_was_dropped(
    routes_near_haifa: respx.Route,
):
    result = await call(radiusKm=10, limit=2)

    assert titles(result) == ["Haifa Trail - Downtown", "Nahal Ovadia"]
    assert "Showing the 2 nearest of 3" in " ".join(result["warnings"])


async def test_the_same_search_twice_gives_the_same_answer(
    routes_near_haifa: respx.Route,
):
    """Determinism is the product guarantee here — an agent's answer should not
    depend on which tile request finished first."""
    first = await call(radiusKm=10)
    second = await call(radiusKm=10)

    assert first == second


def test_routes_at_the_same_distance_are_ordered_by_their_identity():
    def route(source: str, identifier: str, distance: float) -> RouteSummary:
        return RouteSummary(
            ref=FeatureRef(source=source, identifier=identifier),
            title=identifier,
            description=None,
            difficulty=None,
            lengthKm=None,
            startPoint=HAIFA,
            distanceFromSearchCenterKm=distance,
            ihmUrl="",
        )

    shuffled = [
        route("OSM", "relation_2", 1.0),
        route("Nakeb", "9", 1.0),
        route("OSM", "relation_1", 1.0),
        route("OSM", "relation_3", 0.5),
    ]

    assert [r.ref.identifier for r in nearest_first(shuffled)] == [
        "relation_3",
        "9",
        "relation_1",
        "relation_2",
    ]


# --- nothing to return -------------------------------------------------------


async def test_an_area_with_no_routes_is_an_empty_list_not_a_failure(
    mock_upstream: respx.MockRouter,
):
    serve(mock_upstream, {})

    result = await call(radiusKm=5)

    assert result["routes"] == []
    assert "No hiking routes are mapped within 5 km" in " ".join(result["warnings"])


async def test_constraints_that_match_nothing_say_how_many_routes_were_there(
    routes_near_haifa: respx.Route,
):
    result = await call(radiusKm=10, minLengthKm=100)

    assert result["routes"] == []
    warnings = " ".join(result["warnings"])
    assert "3 hiking route(s) are mapped in this area" in warnings
    assert "Relax those" in warnings


# --- the area a search is allowed to cover -----------------------------------


@pytest.mark.parametrize("radius_km", [0.5, 0, -1, 41, 1000])
async def test_a_radius_outside_the_supported_range_is_rejected(radius_km: float):
    text = await call_expecting_error(radiusKm=radius_km)

    assert "radiusKm" in text


async def test_the_widest_search_stays_inside_the_tile_budget(
    mock_upstream: respx.MockRouter,
):
    """40 km is the documented maximum because of what it costs upstream."""
    route = serve(mock_upstream, {})

    await call(radiusKm=40)

    assert route.call_count == len(
        tiles_for_radius(HAIFA, 40, max_tiles=get_settings().max_tiles_per_tool_call)
    )


async def test_a_failing_tile_fails_the_search_with_its_own_code(
    mock_upstream: respx.MockRouter,
):
    """Half an area would read as "there is nothing there"."""
    mock_upstream.get(path__startswith=TILE_ROOT).mock(return_value=httpx.Response(503))

    text = await call_expecting_error(radiusKm=5)

    assert "[upstream_unavailable]" in text


async def test_an_unreadable_tile_keeps_its_own_code(mock_upstream: respx.MockRouter):
    serve(mock_upstream, {HAIFA_TILE: b"this is not a vector tile"})

    text = await call_expecting_error(radiusKm=5)

    assert "[upstream_schema_changed]" in text


# --- the pieces on their own -------------------------------------------------


@pytest.mark.parametrize(
    ("properties", "expected"),
    [
        ({"poiLength": 3504.880687667189}, 3.5),
        ({"poiLength": 51106.61643053978}, 51.11),
        ({"poiLength": "8000"}, 8.0),
        ({"poiLength": 0}, None),
        ({"poiLength": -5}, None),
        ({}, None),
        ({"poiLength": "not a number"}, None),
    ],
)
def test_length_comes_from_metres_and_is_reported_to_ten_metres(
    properties: dict[str, Any], expected: float | None
):
    assert length_km(properties) == expected


@pytest.mark.parametrize(
    ("properties", "language", "expected"),
    [
        ({"name:he": "שביל", "name:en": "Trail", "name": "x"}, "he", "שביל"),
        ({"name:he": "שביל", "name:en": "Trail", "name": "x"}, "en", "Trail"),
        # No Hebrew name: English before the unqualified one, as the map site does.
        ({"name:en": "Trail", "name": "x"}, "he", "Trail"),
        ({"name": "x"}, "he", "x"),
        ({"mtb:name": "Singletrack"}, "en", "Singletrack"),
        ({"name": "   "}, "en", ""),
        ({}, "en", ""),
    ],
)
def test_the_name_falls_back_the_way_the_map_site_does(
    properties: dict[str, Any], language: Language, expected: str
):
    assert tiles.title(properties, language) == expected


def test_an_unconstrained_search_keeps_everything():
    constraints = RouteConstraints()

    assert constraints.length_fits(None)
    assert constraints.length_fits(12.0)
    assert constraints.difficulty_fits(None)
    assert constraints.difficulty_fits("Very Hard")


def test_the_reported_distance_is_a_real_great_circle_one():
    """Straight-line over the ground, not the flat-earth estimate that picks
    tiles: this number is compared against the caller's radius."""
    assert haversine_km(HAIFA, HAIFA) == 0
    assert haversine_km(HAIFA, north_of(HAIFA, 10)) == pytest.approx(10, abs=0.02)
    # A quarter of the way round the earth, where a flat approximation collapses.
    assert haversine_km(
        Coordinates(lat=0, lng=0), Coordinates(lat=0, lng=90)
    ) == pytest.approx(10007.5, abs=1)
