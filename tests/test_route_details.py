"""`get_route_details`, from a share document to the tool's answer.

The share payloads below are built in-test, in the shape a live
`GET /api/urls/{id}` really answered with on 2026-08-14: `dataContainer.routes`
holding segments of `{lat, lng, alt}` points, `length` in metres, `loss` as a
negative number, and `type`/`difficulty` as free strings that can both say
"Unknown". Nothing real is committed — the map's data is CC BY-NC-SA.

Most of it goes through a real client session, because the geometry's shape,
the warnings and the fixed `unknowns` are the contract an agent actually meets.
"""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from ihm_mcp.config import get_settings
from ihm_mcp.models import Coordinates, LineString, MultiLineString
from ihm_mcp.route_sources import activity, climb, length_km
from ihm_mcp.spatial import (
    fit_geometry,
    geometry_of,
    metres_per_degree,
    positions,
    simplify_line,
)
from tests.conftest import connected_session

BASE_URL = str(get_settings().base_url).rstrip("/")

SHARE_ID = "iXKu2M4BV5"
USERS_REF = {"source": "Users", "identifier": SHARE_ID}

#: A few hundred metres of Nahal Galim, near enough to walk on a map.
TRACK = [
    (32.757490, 35.021110),
    (32.757900, 35.022000),
    (32.758310, 35.022900),
    (32.758720, 35.023800),
]


# --- building a share --------------------------------------------------------


def latlngs(points: list[tuple[float, float]]) -> list[dict[str, float]]:
    return [{"lat": lat, "lng": lng, "alt": 440.5} for lat, lng in points]


def segments(*parts: list[tuple[float, float]]) -> list[dict[str, Any]]:
    return [
        {"routingType": "Hike", "routePoint": latlngs(part)[0], "latlngs": latlngs(part)}
        for part in parts
    ]


def share(**overrides: Any) -> dict[str, Any]:
    """One shared route, with every field the live API sends."""
    payload: dict[str, Any] = {
        "id": SHARE_ID,
        "osmUserId": "14569610",
        "title": "נחל גלים",
        "description": "מסלול מעגלי ומאתגר",
        "type": "Hiking",
        "difficulty": "Hard",
        "public": True,
        "length": 6459.700893657341,
        "gain": 255.8955458518581,
        "loss": -251.06433408981323,
        "start": {"lat": TRACK[0][0], "lng": TRACK[0][1], "alt": 440.5},
        "creationDate": "2026-07-15T09:28:04.191Z",
        "lastModifiedDate": "2026-07-15T10:43:11.724Z",
        "dataContainer": {
            "routes": [
                {
                    "id": "hvssjzz",
                    "name": "מסלול",
                    "markers": [],
                    "segments": segments(TRACK),
                }
            ],
            "northEast": {"lat": 32.7583, "lng": 35.0431},
            "southWest": {"lat": 32.7403, "lng": 34.9960},
        },
    }
    return payload | overrides


def straight_track(count: int, *, step: float = 0.00002) -> list[tuple[float, float]]:
    """A recorded track: many points, almost all of them saying nothing."""
    return [(32.75 + index * step, 35.02) for index in range(count)]


def zigzag_track(count: int, *, swing: float = 0.002) -> list[tuple[float, float]]:
    """A track where every single point is a corner, so simplifying keeps them
    all — the shape that no tolerance can bring under the size cap."""
    return [
        (32.75 + index * 0.0005, 35.02 + (swing if index % 2 else -swing))
        for index in range(count)
    ]


@pytest.fixture
def mock_upstream() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


def serve(
    router: respx.MockRouter, payload: dict[str, Any], *, share_id: str = SHARE_ID
) -> respx.Route:
    return router.get(f"/api/urls/{share_id}").mock(
        return_value=httpx.Response(200, json=payload)
    )


@pytest.fixture
def shared_route(mock_upstream: respx.MockRouter) -> respx.Route:
    return serve(mock_upstream, share())


async def call(ref: dict[str, str] | None = None, **arguments: Any) -> dict[str, Any]:
    async with connected_session() as session:
        result = await session.call_tool(
            "get_route_details", {"route": ref or USERS_REF, **arguments}
        )

    assert result.isError is False, result.content
    assert result.structuredContent is not None
    return result.structuredContent


async def call_expecting_error(ref: dict[str, str] | None = None, **arguments: Any) -> str:
    async with connected_session() as session:
        result = await session.call_tool(
            "get_route_details", {"route": ref or USERS_REF, **arguments}
        )

    assert result.isError is True
    return "\n".join(getattr(block, "text", "") for block in result.content)


# --- what comes back ---------------------------------------------------------


async def test_a_share_comes_back_as_a_line_with_what_the_data_records(
    shared_route: respx.Route,
):
    result = await call()

    assert result["ref"] == USERS_REF
    assert result["title"] == "נחל גלים"
    assert result["description"] == "מסלול מעגלי ומאתגר"
    assert result["activity"] == "Hiking"
    assert result["difficulty"] == "Hard"
    assert result["lengthKm"] == pytest.approx(6.46)
    assert result["ascentMeters"] == 256
    assert result["descentMeters"] == 251
    assert result["geometry"]["type"] == "LineString"
    assert result["ihmUrl"] == f"{BASE_URL}/share/{SHARE_ID}"
    assert "CC BY-NC-SA" in " ".join(result["attribution"]["sources"])


async def test_the_geometry_is_geojson_positions_longitude_first(
    shared_route: respx.Route,
):
    """The one field in this server that is not `lat, lng` — because GeoJSON."""
    result = await call()

    first, *_ = result["geometry"]["coordinates"]
    assert first == [35.02111, 32.75749]
    assert result["startPoint"] == {"lat": 32.75749, "lng": 35.02111}


async def test_the_editors_segments_are_joined_into_one_line(
    mock_upstream: respx.MockRouter,
):
    """A route is cut into a segment per point the user dropped, and each
    segment restates the previous one's last point."""
    serve(
        mock_upstream,
        share(
            dataContainer={
                "routes": [{"segments": segments(TRACK[:2], TRACK[1:3], TRACK[2:])}]
            }
        ),
    )

    result = await call()

    assert result["geometry"]["type"] == "LineString"
    assert len(result["geometry"]["coordinates"]) == len(TRACK)
    assert result["geometryDetail"]["recordedPointCount"] == len(TRACK)


async def test_a_share_holding_several_routes_becomes_a_multilinestring(
    mock_upstream: respx.MockRouter,
):
    serve(
        mock_upstream,
        share(
            dataContainer={
                "routes": [
                    {"name": "Day 1", "segments": segments(TRACK[:2])},
                    {"name": "Day 2", "segments": segments(TRACK[2:])},
                ]
            }
        ),
    )

    result = await call()

    assert result["geometry"]["type"] == "MultiLineString"
    assert [len(line) for line in result["geometry"]["coordinates"]] == [2, 2]
    assert "2 separate lines" in " ".join(result["warnings"])


async def test_a_route_with_no_line_is_left_out_of_the_geometry(
    mock_upstream: respx.MockRouter,
):
    """A share can hold a route somebody started and abandoned at one point."""
    serve(
        mock_upstream,
        share(
            dataContainer={
                "routes": [
                    {"name": "Real", "segments": segments(TRACK)},
                    {"name": "Abandoned", "segments": segments(TRACK[:1])},
                ]
            }
        ),
    )

    result = await call()

    assert result["geometry"]["type"] == "LineString"
    assert len(result["geometry"]["coordinates"]) == len(TRACK)


async def test_every_answer_carries_the_same_unknowns_and_the_sources_note(
    shared_route: respx.Route,
):
    result = await call()

    unknowns = " ".join(result["unknowns"])
    assert "open, permitted or passable" in unknowns
    assert "drinking water" in unknowns
    assert "waymarked" in unknowns
    assert "one person's saved and shared route" in " ".join(result["warnings"])


async def test_the_start_point_upstream_states_is_preferred(
    mock_upstream: respx.MockRouter,
):
    """Stated to the same precision as the line, not to upstream's fourteen
    digits of float noise."""
    serve(mock_upstream, share(start={"lat": 32.75749082134003, "lng": 35.0}))

    result = await call()

    assert result["startPoint"] == {"lat": 32.757491, "lng": 35.0}


async def test_a_share_with_no_stated_start_falls_back_to_the_line(
    mock_upstream: respx.MockRouter,
):
    serve(mock_upstream, share(start=None))

    result = await call()

    assert result["startPoint"] == {"lat": 32.75749, "lng": 35.02111}


# --- what the data does not say ----------------------------------------------


async def test_upstreams_own_word_for_unrated_is_reported_as_nothing(
    mock_upstream: respx.MockRouter,
):
    """`difficulty` and `type` arrive as the literal string "Unknown"."""
    serve(mock_upstream, share(difficulty="Unknown", type="Unknown"))

    result = await call()

    assert result["difficulty"] is None
    assert result["activity"] is None


async def test_a_difficulty_outside_upstreams_scale_is_reported_as_nothing(
    mock_upstream: respx.MockRouter,
):
    serve(mock_upstream, share(difficulty="Extreme"))

    result = await call()

    assert result["difficulty"] is None


async def test_measurements_the_share_never_recorded_come_back_null(
    mock_upstream: respx.MockRouter,
):
    """Upstream writes zero for a route it never measured."""
    serve(mock_upstream, share(length=0, gain=None, loss=None, description="   "))

    result = await call()

    assert result["lengthKm"] is None
    assert result["ascentMeters"] is None
    assert result["descentMeters"] is None
    assert result["description"] is None


async def test_an_untitled_share_is_named_by_its_id_not_left_blank(
    mock_upstream: respx.MockRouter,
):
    serve(mock_upstream, share(title=None))

    result = await call()

    assert result["title"] == f"Shared route {SHARE_ID}"


# --- geometry that does not fit ----------------------------------------------


async def test_a_recorded_track_is_thinned_and_says_so(mock_upstream: respx.MockRouter):
    track = straight_track(4_000)
    serve(mock_upstream, share(dataContainer={"routes": [{"segments": segments(track)}]}))

    result = await call()
    detail = result["geometryDetail"]
    coordinates = result["geometry"]["coordinates"]

    assert detail["recordedPointCount"] == 4_000
    assert detail["simplified"] is True
    assert detail["toleranceMeters"] == 10
    assert detail["pointCount"] == len(coordinates) <= 3_000
    # The ends are where the recording started and stopped, still.
    assert coordinates[0] == [35.02, round(track[0][0], 6)]
    assert coordinates[-1] == [35.02, round(track[-1][0], 6)]


async def test_a_route_that_fits_is_returned_exactly_as_recorded(
    shared_route: respx.Route,
):
    result = await call()

    assert result["geometryDetail"] == {
        "pointCount": 4,
        "recordedPointCount": 4,
        "simplified": False,
        "toleranceMeters": None,
    }
    assert result["warnings"] == [
        warning for warning in result["warnings"] if "thinned" not in warning
    ]


async def test_a_shape_no_tolerance_can_shrink_is_refused_not_truncated(
    mock_upstream: respx.MockRouter,
):
    """Half a route is a different route, and a caller reading the end of one
    would never know."""
    serve(
        mock_upstream,
        share(dataContainer={"routes": [{"segments": segments(zigzag_track(3_200))}]}),
    )

    text = await call_expecting_error()

    assert "[geometry_too_large]" in text


# --- refs this server cannot resolve -----------------------------------------


@pytest.mark.parametrize("source", ["Nakeb", "iNature", "Wikidata", "made-up"])
async def test_a_source_with_no_adapter_is_refused_by_name(source: str):
    text = await call_expecting_error({"source": source, "identifier": "relation_282071"})

    assert "[unsupported_source]" in text
    assert source in text
    assert "OSM, Users" in text


async def test_a_share_that_does_not_exist_is_route_not_found(
    mock_upstream: respx.MockRouter,
):
    mock_upstream.get(path__startswith="/api/urls/").mock(return_value=httpx.Response(404))

    text = await call_expecting_error()

    assert "[route_not_found]" in text


async def test_a_share_holding_no_route_line_is_route_not_found(
    mock_upstream: respx.MockRouter,
):
    """A share is a saved map view: it can hold markers and no route at all."""
    serve(mock_upstream, share(dataContainer={"routes": [], "northEast": None}))

    text = await call_expecting_error()

    assert "[route_not_found]" in text
    assert "no route line" in text


async def test_an_empty_identifier_is_refused_before_asking_upstream(
    mock_upstream: respx.MockRouter,
):
    route = mock_upstream.get(path__startswith="/api/urls/")

    text = await call_expecting_error({"source": "Users", "identifier": "  "})

    assert "[invalid_input]" in text
    assert route.call_count == 0


async def test_a_share_in_an_unrecognised_shape_is_not_guessed_at(
    mock_upstream: respx.MockRouter,
):
    serve(mock_upstream, {"id": SHARE_ID, "dataContainer": {"routes": "nonsense"}})

    text = await call_expecting_error()

    assert "[upstream_schema_changed]" in text


async def test_an_upstream_outage_keeps_its_own_code(mock_upstream: respx.MockRouter):
    mock_upstream.get(path__startswith="/api/urls/").mock(return_value=httpx.Response(503))

    text = await call_expecting_error()

    assert "[upstream_unavailable]" in text


# --- the pieces on their own -------------------------------------------------


def test_repeated_positions_at_a_segment_join_are_dropped_once_rounded():
    point = Coordinates(lat=32.757490821, lng=35.021110287)
    almost = Coordinates(lat=32.7574908, lng=35.0211103)
    elsewhere = Coordinates(lat=32.7579, lng=35.022)

    assert positions([point, almost, elsewhere]) == [
        (35.02111, 32.757491),
        (35.022, 32.7579),
    ]


def test_a_line_of_fewer_than_two_positions_draws_no_geometry():
    assert geometry_of([]) is None
    assert geometry_of([[(35.0, 32.7)]]) is None
    assert isinstance(geometry_of([[(35.0, 32.7), (35.1, 32.8)]]), LineString)
    assert isinstance(
        geometry_of([[(35.0, 32.7), (35.1, 32.8)], [(34.0, 31.7), (34.1, 31.8)]]),
        MultiLineString,
    )


def test_simplifying_keeps_the_ends_and_the_corners():
    """A dogleg with a redundant point in the middle of each straight."""
    line = [
        (35.0, 32.70),
        (35.0, 32.71),  # redundant: dead straight
        (35.0, 32.72),
        (35.01, 32.72),
    ]

    assert simplify_line(line, 10.0, latitude=32.71) == [
        (35.0, 32.70),
        (35.0, 32.72),
        (35.01, 32.72),
    ]


def test_a_metre_is_measured_the_same_way_east_and_north():
    east, north = metres_per_degree(32.75)

    assert north == pytest.approx(111_195, abs=5)
    # A degree of longitude is shorter here by the cosine of the latitude.
    assert east == pytest.approx(north * 0.8410, rel=1e-3)


def test_a_geometry_within_the_cap_is_returned_untouched():
    walk = [Coordinates(lat=32.7 + step / 1000, lng=35.0) for step in range(50)]
    geometry = geometry_of([positions(walk)])
    assert geometry is not None

    fitted, detail = fit_geometry(geometry)

    assert fitted == geometry
    assert detail.simplified is False
    assert detail.toleranceMeters is None
    assert detail.pointCount == detail.recordedPointCount == 50


@pytest.mark.parametrize(
    ("metres", "expected"), [(6459.7, 6.46), (0, None), (-1, None), (None, None)]
)
def test_a_shares_length_comes_from_metres(metres: float | None, expected: float | None):
    assert length_km(metres) == expected


@pytest.mark.parametrize(
    ("metres", "expected"), [(255.89, 256), (-251.06, 251), (0, None), (None, None)]
)
def test_climb_is_reported_as_whole_positive_metres(
    metres: float | None, expected: int | None
):
    assert climb(metres) == expected


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("Hiking", "Hiking"), ("4x4", "4x4"), ("Unknown", None), ("", None), (None, None)],
)
def test_the_activity_is_upstreams_word_unless_it_is_a_shrug(
    kind: str | None, expected: str | None
):
    assert activity(kind) == expected
