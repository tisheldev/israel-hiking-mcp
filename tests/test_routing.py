"""`route_between_points`, from two coordinates to a calculated line.

The fixture path runs due east at one latitude, so its length is the sum of the
offsets it was built from and every assertion below is a hand-computed number
rather than one read off a run.

What the rest of the file is about is the difference between this tool and the
others: its answer is computed, not recorded. So the tests that matter are the
ones about what the answer is allowed to claim — that the calculated-path
warning is there whatever else happened, that an end snapped half a kilometre
away says so, and that a path calculated for the wrong activity is refused
rather than returned. Upstream answers an unrecognised `type=` with a walking
route instead of an error, which makes that last one a real failure mode and
not a hypothetical.
"""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from ihm_mcp.config import get_settings
from ihm_mcp.models import Coordinates
from ihm_mcp.spatial import METRES_PER_KM, haversine_km, metres_per_degree
from ihm_mcp.tools.routing import CALCULATED_PATH, MAX_DISTANCE_KM
from tests.conftest import connected_session

BASE_URL = str(get_settings().base_url).rstrip("/")
ROUTING = "/api/routing"

HAIFA = Coordinates(lat=32.8191, lng=34.9984)
#: Well inside the map's coverage, and 100 km from nothing in these tests.
CARMEL = Coordinates(lat=32.7500, lng=35.0300)
EILAT = Coordinates(lat=29.5500, lng=34.9500)
CYPRUS = Coordinates(lat=34.9000, lng=33.0000)


def east_of(point: Coordinates, metres: float) -> Coordinates:
    return Coordinates(
        lat=point.lat, lng=point.lng + metres / metres_per_degree(point.lat)[0]
    )


def north_of(point: Coordinates, metres: float) -> Coordinates:
    return Coordinates(
        lat=point.lat + metres / metres_per_degree(point.lat)[1], lng=point.lng
    )


#: 2 km due east of Haifa in four steps, so the calculated path is 2 km long.
TRACK = [east_of(HAIFA, along) for along in (0, 500, 1000, 1500, 2000)]
DESTINATION = TRACK[-1]


# --- the upstream answer ------------------------------------------------------


def collection(
    track: list[Coordinates] = TRACK,
    *,
    profile: str = "Foot",
    elevation: bool = True,
    **overrides: Any,
) -> dict[str, Any]:
    """One calculated path, in the shape `/api/routing` really answers with.

    Positions carry a third ordinate, because the live endpoint sends one
    whenever its elevation service answered.
    """
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [point.lng, point.lat, 120.0]
                        if elevation
                        else [point.lng, point.lat]
                        for point in track
                    ],
                },
                "properties": {
                    "Name": f"Routing from a to b profile type: {profile}",
                    "Creator": "Mapeak",
                },
                **overrides,
            }
        ],
    }


@pytest.fixture
def mock_upstream() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


def serve(router: respx.MockRouter, payload: Any = None) -> respx.MockRouter:
    router.get(path=ROUTING).mock(
        return_value=httpx.Response(
            200, json=payload if payload is not None else collection()
        )
    )
    return router


@pytest.fixture
def a_calculated_path(mock_upstream: respx.MockRouter) -> respx.MockRouter:
    return serve(mock_upstream)


async def call(**arguments: Any) -> dict[str, Any]:
    async with connected_session() as session:
        result = await session.call_tool(
            "route_between_points",
            {
                "start": HAIFA.model_dump(),
                "end": DESTINATION.model_dump(),
                **arguments,
            },
        )

    assert result.isError is False, result.content
    assert result.structuredContent is not None
    return result.structuredContent


async def call_expecting_error(**arguments: Any) -> str:
    async with connected_session() as session:
        result = await session.call_tool(
            "route_between_points",
            {
                "start": HAIFA.model_dump(),
                "end": DESTINATION.model_dump(),
                **arguments,
            },
        )

    assert result.isError is True
    return "\n".join(getattr(block, "text", "") for block in result.content)


# --- the path itself ----------------------------------------------------------


async def test_the_path_comes_back_as_geojson_in_longitude_latitude_order(
    a_calculated_path: respx.MockRouter,
):
    result = await call()

    assert result["geometry"]["type"] == "LineString"
    first = result["geometry"]["coordinates"][0]
    assert first == pytest.approx([HAIFA.lng, HAIFA.lat], abs=1e-6)


async def test_upstreams_elevation_is_dropped_from_every_position(
    a_calculated_path: respx.MockRouter,
):
    """It is interpolated from a sampled profile, and nothing here would be
    honest to compute from it — so it is not carried as if it were data."""
    result = await call()

    assert all(len(position) == 2 for position in result["geometry"]["coordinates"])


async def test_the_length_is_the_line_measured_on_the_ground(
    a_calculated_path: respx.MockRouter,
):
    """Hand-computed: four 500 m steps due east."""
    result = await call()

    assert result["lengthKm"] == pytest.approx(2.0, abs=0.01)


async def test_the_straight_line_is_reported_beside_the_path(
    a_calculated_path: respx.MockRouter,
):
    """Together they say how far round the mapped ways go — here, not at all."""
    result = await call()

    assert result["straightLineKm"] == pytest.approx(2.0, abs=0.01)


async def test_a_path_that_doubles_back_is_longer_than_the_straight_line(
    mock_upstream: respx.MockRouter,
):
    detour = [HAIFA, north_of(HAIFA, 1000), north_of(DESTINATION, 1000), DESTINATION]
    serve(mock_upstream, collection(detour))

    result = await call()

    assert result["straightLineKm"] == pytest.approx(2.0, abs=0.01)
    assert result["lengthKm"] == pytest.approx(4.0, abs=0.02)


async def test_the_answer_carries_its_attribution(a_calculated_path: respx.MockRouter):
    result = await call()

    assert "CC BY-NC-SA" in " ".join(result["attribution"]["sources"])
    assert "OpenStreetMap" in " ".join(result["attribution"]["sources"])


async def test_the_same_request_twice_gives_the_same_answer(
    a_calculated_path: respx.MockRouter,
):
    assert await call() == await call()


# --- what the answer is allowed to claim --------------------------------------


async def test_every_answer_leads_with_the_calculated_path_warning(
    a_calculated_path: respx.MockRouter,
):
    """Unconditional: it warns about what the tool is, not about what went
    wrong with a particular call."""
    result = await call()

    assert result["warnings"][0] == CALCULATED_PATH
    assert "not a route anybody has walked or checked" in result["warnings"][0]


async def test_the_unknowns_are_returned_whole_with_every_answer(
    a_calculated_path: respx.MockRouter,
):
    result = await call()

    assert len(result["unknowns"]) == 4
    assert "live-fire zones" in result["unknowns"][0]
    assert "No duration is returned" in result["unknowns"][1]


async def test_the_activity_asked_for_is_echoed_back(
    a_calculated_path: respx.MockRouter,
):
    result = await call()

    assert result["activity"] == "Hiking"


# --- the ends the router could actually reach ---------------------------------


async def test_both_ends_report_what_was_asked_and_what_was_reached(
    a_calculated_path: respx.MockRouter,
):
    result = await call()

    assert result["start"]["requested"]["lat"] == pytest.approx(HAIFA.lat)
    assert result["start"]["onPath"]["lat"] == pytest.approx(HAIFA.lat, abs=1e-6)
    assert result["start"]["metersApart"] == 0
    assert result["end"]["metersApart"] == 0


async def test_an_end_the_router_moved_says_how_far_it_moved(
    mock_upstream: respx.MockRouter,
):
    """A router can only start on a way it knows, and in open country the
    nearest one can be a long walk from the point asked for."""
    serve(mock_upstream, collection([north_of(HAIFA, 400), *TRACK[1:]]))

    result = await call()

    assert result["start"]["metersApart"] == pytest.approx(400, abs=2)
    assert "The path starts 400 m from the point asked for" in " ".join(
        result["warnings"]
    )


async def test_a_few_metres_of_snapping_is_not_worth_a_sentence(
    mock_upstream: respx.MockRouter,
):
    """Twenty metres is a road's width; warning about it would train a reader
    to skip the warnings that matter."""
    serve(mock_upstream, collection([north_of(HAIFA, 20), *TRACK[1:]]))

    result = await call()

    assert result["start"]["metersApart"] == pytest.approx(20, abs=2)
    assert not any(
        "from the point asked for" in warning for warning in result["warnings"]
    )


# --- the activity, which upstream will not check for us -----------------------


@pytest.mark.parametrize(
    ("activity", "sent"),
    [("Hiking", "Hike"), ("Bicycle", "Bike"), ("4x4", "4WD")],
)
async def test_each_activity_is_sent_as_the_word_upstream_takes(
    mock_upstream: respx.MockRouter, activity: str, sent: str
):
    profiles = {"Hike": "Foot", "Bike": "Bike", "4WD": "Car4WheelDrive"}
    route = mock_upstream.get(path=ROUTING).mock(
        return_value=httpx.Response(200, json=collection(profile=profiles[sent]))
    )

    await call(activity=activity)

    assert route.calls.last.request.url.params["type"] == sent


async def test_an_activity_outside_the_three_is_rejected_at_the_schema_boundary():
    text = await call_expecting_error(activity="Kayak")

    assert "activity" in text


async def test_a_path_calculated_for_something_else_is_refused(
    mock_upstream: respx.MockRouter,
):
    """Upstream answers an unrecognised `type=` with a walking route rather
    than an error, so a renamed constant would silently turn every cycling
    request into a footpath. That is a changed contract, not an answer."""
    serve(mock_upstream, collection(profile="Foot"))

    text = await call_expecting_error(activity="Bicycle")

    assert "[upstream_schema_changed]" in text
    assert "calculated a Foot one instead" in text


async def test_a_profile_named_in_words_this_server_does_not_know_is_not_a_mismatch(
    mock_upstream: respx.MockRouter,
):
    """The check reads a sentence upstream writes for people. Reworded, it says
    nothing about which profile ran — and refusing on that would fail calls
    that are perfectly fine."""
    serve(mock_upstream, collection(profile="pedestrian-v2"))

    result = await call()

    assert result["lengthKm"] == pytest.approx(2.0, abs=0.01)


# --- what this server will ask for --------------------------------------------


@pytest.mark.parametrize("end", [CYPRUS])
async def test_a_point_outside_the_area_the_map_covers_is_refused(end: Coordinates):
    text = await call_expecting_error(end=end.model_dump())

    assert "[invalid_input]" in text
    assert "`end`" in text
    assert "outside the area this map covers" in text


async def test_the_refusal_names_the_end_that_is_outside():
    text = await call_expecting_error(start=CYPRUS.model_dump())

    assert "`start`" in text


async def test_two_points_further_apart_than_this_tool_routes_are_refused():
    """Haifa to Eilat is a trek, not a path to calculate turn by turn."""
    text = await call_expecting_error(end=EILAT.model_dump())

    assert "[invalid_input]" in text
    assert f"routes up to {MAX_DISTANCE_KM:g} km" in text
    assert haversine_km(HAIFA, EILAT) > MAX_DISTANCE_KM


async def test_the_same_point_twice_is_refused_rather_than_routed():
    """Upstream answers it with one position repeated, which is not a path."""
    text = await call_expecting_error(end=HAIFA.model_dump())

    assert "[invalid_input]" in text
    assert "the same place" in text


async def test_a_coordinate_off_the_earth_is_rejected_at_the_schema_boundary():
    text = await call_expecting_error(end={"lat": 91.0, "lng": 34.0})

    assert "lat" in text


async def test_a_refusal_costs_no_upstream_request(mock_upstream: respx.MockRouter):
    route = serve(mock_upstream).routes[0]

    await call_expecting_error(end=EILAT.model_dump())

    assert route.call_count == 0


# --- when upstream cannot answer ----------------------------------------------


async def test_no_path_between_the_points_is_reported_as_a_missing_route(
    mock_upstream: respx.MockRouter,
):
    serve(mock_upstream, {"type": "FeatureCollection", "features": []})

    text = await call_expecting_error()

    assert "[route_not_found]" in text
    assert "found no path between these points" in text


async def test_a_single_position_is_not_a_path(mock_upstream: respx.MockRouter):
    serve(mock_upstream, collection([HAIFA, HAIFA]))

    text = await call_expecting_error()

    assert "[route_not_found]" in text


async def test_a_shape_that_is_not_a_line_is_reported_rather_than_read(
    mock_upstream: respx.MockRouter,
):
    """A geometry whose positions still parse, but which is not a path. One
    nested differently — a Polygon's rings — never gets this far: it fails
    validation, and comes back as the same typed error a sentence earlier."""
    payload = collection()
    payload["features"][0]["geometry"] = {
        "type": "MultiPoint",
        "coordinates": [[34.9, 32.8], [35.0, 32.9]],
    }
    serve(mock_upstream, payload)

    text = await call_expecting_error()

    assert "[upstream_schema_changed]" in text
    assert "MultiPoint" in text


async def test_a_position_that_is_not_a_coordinate_pair_is_reported(
    mock_upstream: respx.MockRouter,
):
    payload = collection()
    payload["features"][0]["geometry"]["coordinates"] = [[34.9], [35.0, 32.8]]
    serve(mock_upstream, payload)

    text = await call_expecting_error()

    assert "[upstream_schema_changed]" in text


async def test_a_body_in_a_shape_this_server_does_not_recognise_is_discarded(
    mock_upstream: respx.MockRouter,
):
    serve(mock_upstream, {"features": "a path, honestly"})

    text = await call_expecting_error()

    assert "[upstream_schema_changed]" in text
    assert "discarded rather than guessed at" in text


async def test_the_endpoint_going_missing_is_not_a_missing_route(
    mock_upstream: respx.MockRouter,
):
    """A 404 here means the endpoint moved: it answers with a path or not at
    all, never with 'no such route'."""
    mock_upstream.get(path=ROUTING).mock(return_value=httpx.Response(404))

    text = await call_expecting_error()

    assert "[upstream_schema_changed]" in text
    assert "routing endpoint is gone" in text


async def test_a_host_that_goes_quiet_is_a_timeout(mock_upstream: respx.MockRouter):
    """The live endpoint answers an unroutable pair by never answering."""
    mock_upstream.get(path=ROUTING).mock(side_effect=httpx.ReadTimeout("slow"))

    text = await call_expecting_error()

    assert "[upstream_timeout]" in text


async def test_a_failing_host_is_reported_as_unavailable(
    mock_upstream: respx.MockRouter,
):
    mock_upstream.get(path=ROUTING).mock(return_value=httpx.Response(500))

    text = await call_expecting_error()

    assert "[upstream_unavailable]" in text


# --- a path too detailed to hand over -----------------------------------------


def long_track() -> list[Coordinates]:
    """3,500 positions 20 m apart: 70 km, inside what this tool will route."""
    return [east_of(HAIFA, along * 20) for along in range(3_500)]


async def test_a_long_path_is_thinned_and_says_by_how_much(
    mock_upstream: respx.MockRouter,
):
    track = long_track()
    serve(mock_upstream, collection(track))

    result = await call(end=track[-1].model_dump())

    detail = result["geometryDetail"]
    assert detail["recordedPointCount"] == 3_500
    assert detail["pointCount"] < 3_500
    assert detail["simplified"] is True
    assert "was thinned from 3,500 calculated positions" in " ".join(result["warnings"])


async def test_the_length_is_measured_before_the_thinning(
    mock_upstream: respx.MockRouter,
):
    """`lengthKm` describes the path, not the drawing of it that fitted."""
    track = long_track()
    serve(mock_upstream, collection(track))

    result = await call(end=track[-1].model_dump())

    assert result["lengthKm"] == pytest.approx(
        (len(track) - 1) * 20 / METRES_PER_KM, abs=0.05
    )
