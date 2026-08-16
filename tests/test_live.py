"""The acceptance run, against the real hosts. Deselected unless asked for.

`uv run pytest` never runs this file — `addopts = "-m 'not live'"` in
`pyproject.toml` deselects it — so the suite stays offline, hermetic and
fast, and nothing here reaches a volunteer-run service by accident. Run it
deliberately:

    uv run pytest -m live

What it is for is the class of bug no fixture can catch: upstream changing a
field name, renaming a routing type, moving an endpoint, or thinning a tileset.
Every other test in this repo is written against a shape captured on a date, and
this is the one that asks whether that shape is still true.

So the assertions are about **contract, not content**. Which routes are mapped
near Haifa is a fact about the world, and one that changes when somebody edits
the map; that a route comes back with an identity, a name, a link and its
cautions is a fact about this server. A test that pinned the first result would
fail for the wrong reason within a month.

Two things here can fail without this server being wrong: a shared route can be
deleted by its author, and an OSM relation can be split or renumbered by a
mapper. Both are skipped with the reason rather than failed, because neither is
something a change here could fix.

One live run costs about a dozen requests across mapeak.com and
api.openstreetmap.org. Keep it that way.
"""

from typing import Any

import pytest

from ihm_mcp.pois import WATER_CAUTION
from ihm_mcp.tools.routing import CALCULATED_PATH
from tests.conftest import connected_session

pytestmark = pytest.mark.live

#: Haifa, from the map's own search. The Carmel is the densest hiking area in
#: the country, so it is where an empty answer really would mean something broke.
HAIFA = {"lat": 32.8191218, "lng": 34.9983856}

#: The Haifa Trail: a relation of sub-relations, which is the interesting shape.
HAIFA_TRAIL = {"source": "OSM", "identifier": "relation_13207704"}

#: A route somebody drew and shared on the map site, 6.5 km on the Carmel.
SHARED_ROUTE = {"source": "Users", "identifier": "iXKu2M4BV5"}


async def call(name: str, **arguments: Any) -> dict[str, Any]:
    async with connected_session() as session:
        result = await session.call_tool(name, arguments)

    text = "\n".join(getattr(block, "text", "") for block in result.content)
    if result.isError and "[route_not_found]" in text:
        pytest.skip(f"the feature this test names is gone upstream: {text}")
    assert result.isError is False, text
    assert result.structuredContent is not None
    return result.structuredContent


# --- search_places -------------------------------------------------------------


async def test_a_place_search_finds_haifa_in_israel_first():
    result = await call("search_places", query="Haifa")

    assert result["places"], "the map's search returned nothing for Haifa"
    first = result["places"][0]
    assert first["inIsrael"] is True
    assert "Haifa" in first["displayName"]
    assert first["ihmUrl"].startswith("https://mapeak.com/poi/")
    assert first["ref"]["source"] and first["ref"]["identifier"]


async def test_a_hebrew_place_search_survives_the_round_trip():
    """The term is URL-encoded on the way out and comes back in Hebrew."""
    result = await call("search_places", query="עין חמד", language="he")

    assert result["places"]
    assert result["query"] == "עין חמד"


# --- search_hiking_routes ------------------------------------------------------


async def test_hiking_routes_are_mapped_around_haifa():
    result = await call(
        "search_hiking_routes", center=HAIFA, radiusKm=15, minLengthKm=4, maxLengthKm=12
    )

    assert result["routes"], "no hiking routes within 15 km of Haifa"
    assert result["searchedArea"] == {"center": HAIFA, "radiusKm": 15}
    for route in result["routes"]:
        assert route["title"]
        assert route["ref"]["source"] and route["ref"]["identifier"]
        assert route["ihmUrl"].startswith("https://mapeak.com/")
        assert route["distanceFromSearchCenterKm"] <= 15
        assert route["lengthKm"] is None or 4 <= route["lengthKm"] <= 12
    # Nearest first, and the same list twice — the guarantee the tool makes.
    distances = [route["distanceFromSearchCenterKm"] for route in result["routes"]]
    assert distances == sorted(distances)


async def test_the_same_search_twice_returns_the_same_routes():
    first = await call("search_hiking_routes", center=HAIFA, radiusKm=10)
    second = await call("search_hiking_routes", center=HAIFA, radiusKm=10)

    assert first == second


# --- get_route_details ---------------------------------------------------------


async def test_an_osm_relation_resolves_to_a_line():
    result = await call("get_route_details", route=HAIFA_TRAIL)

    assert result["ref"] == HAIFA_TRAIL
    assert result["title"]
    assert result["geometry"]["type"] in ("LineString", "MultiLineString")
    assert result["geometryDetail"]["recordedPointCount"] >= 2
    assert result["unknowns"], "a route with no unknowns has stopped saying what it is"
    assert any("OpenStreetMap" in warning for warning in result["warnings"])
    # GeoJSON order: longitude first, so a route here has lng ≈ 35 and lat ≈ 32.
    lines = (
        [result["geometry"]["coordinates"]]
        if result["geometry"]["type"] == "LineString"
        else result["geometry"]["coordinates"]
    )
    for lng, lat in lines[0]:
        assert 34.0 < lng < 36.0 and 29.0 < lat < 34.0


async def test_a_shared_route_resolves_with_its_authors_own_words():
    result = await call("get_route_details", route=SHARED_ROUTE)

    assert result["title"]
    assert result["ihmUrl"] == "https://mapeak.com/share/iXKu2M4BV5"
    assert result["geometry"]["coordinates"]
    assert any("one person's saved" in warning for warning in result["warnings"])


async def test_a_source_this_server_cannot_resolve_is_still_refused_by_name():
    """No request goes anywhere for this one; it is here so the live run covers
    the answer a caller gets for the sources upstream has and this server does not."""
    async with connected_session() as session:
        result = await session.call_tool(
            "get_route_details", {"route": {"source": "Nakeb", "identifier": "255"}}
        )

    assert result.isError is True
    assert "[unsupported_source]" in "\n".join(
        getattr(block, "text", "") for block in result.content
    )


# --- find_pois_along_route -----------------------------------------------------


async def test_water_along_the_haifa_trail_comes_back_with_its_caution():
    result = await call(
        "find_pois_along_route", route=HAIFA_TRAIL, bufferMeters=1000, categories=["Water"]
    )

    assert result["route"]["ref"] == HAIFA_TRAIL
    assert result["unknowns"]
    if not result["pois"]:  # pragma: no cover - the map would have to lose them
        pytest.fail("no water is mapped within 1 km of the Haifa Trail")

    for poi in result["pois"]:
        assert poi["category"] == "Water"
        assert poi["caution"] == WATER_CAUTION
        assert poi["evidence"]["freshnessKnown"] is False
        assert poi["distanceFromRouteMeters"] <= 1000
    # The caution heads the warnings as well as riding on each point.
    assert WATER_CAUTION in result["warnings"][0]
    distances = [poi["distanceFromRouteMeters"] for poi in result["pois"]]
    assert distances == sorted(distances)


# --- route_between_points ------------------------------------------------------

#: Two points on the Carmel, about 5 km apart, with mapped ways between them.
FROM = {"lat": 32.8191, "lng": 34.9984}
TO = {"lat": 32.7800, "lng": 35.0200}


async def test_a_walking_path_is_calculated_between_two_points():
    result = await call("route_between_points", start=FROM, end=TO, activity="Hiking")

    assert result["activity"] == "Hiking"
    assert result["geometry"]["type"] == "LineString"
    assert len(result["geometry"]["coordinates"]) >= 2
    # A path over mapped ways is never shorter than the straight line.
    assert result["lengthKm"] >= result["straightLineKm"]
    assert result["warnings"][0] == CALCULATED_PATH
    assert result["unknowns"]
    assert result["start"]["requested"] == FROM
    assert result["end"]["requested"] == TO


@pytest.mark.parametrize("activity", ["Hiking", "Bicycle", "4x4"])
async def test_upstream_still_routes_for_the_activity_it_is_asked_for(activity: str):
    """The guard that only a live run can exercise. Upstream answers a routing
    type it does not recognise with a walking route rather than an error, so a
    renamed constant would silently turn every cycling request into a footpath —
    and this server refuses a mismatch, which makes it a failure here."""
    result = await call("route_between_points", start=FROM, end=TO, activity=activity)

    assert result["activity"] == activity
    assert result["lengthKm"] > 0
