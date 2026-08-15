"""`get_route_details` for an OSM route, from elements to one line.

The payloads below are built in-test in the shape `api.openstreetmap.org`
answers `/api/0.6/{type}/{id}/full.json` with: a flat `elements` list holding
nodes with `lat`/`lon`, ways with an ordered `nodes` list, and relations with
ordered `members` — plus the version/changeset/user fields this server ignores,
so that ignoring them is tested rather than assumed. Nothing real is committed:
OSM's data is ODbL, and synthetic ways make the joins legible.

The route in them is three ways that chain into one line, one of them mapped in
the opposite direction — which is the whole reason this source needs assembling
at all.
"""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from ihm_mcp.config import get_settings
from ihm_mcp.osm import OsmFeature
from ihm_mcp.route_sources import osm_activity, osm_difficulty, osm_length_km
from ihm_mcp.spatial import merge_lines
from tests.conftest import connected_session

settings = get_settings()
BASE_URL = str(settings.base_url).rstrip("/")
OSM_URL = str(settings.osm_api_url).rstrip("/")

RELATION = 282071
OSM_REF = {"source": "OSM", "identifier": f"relation_{RELATION}"}


# --- building an OSM answer --------------------------------------------------

#: Seven nodes up Nahal Galim, in the order a walker would pass them.
NODES = {
    1: (32.757490, 35.021110),
    2: (32.757900, 35.022000),
    3: (32.758310, 35.022900),
    4: (32.758720, 35.023800),
    5: (32.759130, 35.024700),
    6: (32.759540, 35.025600),
    7: (32.759950, 35.026500),
}

#: Somewhere else entirely, so a way built from these cannot chain onto the rest.
ELSEWHERE = {8: (31.771959, 35.217018), 9: (31.772400, 35.218000)}


def node(node_id: int) -> dict[str, Any]:
    lat, lon = (NODES | ELSEWHERE)[node_id]
    return {
        "type": "node",
        "id": node_id,
        "lat": lat,
        "lon": lon,
        "version": 3,
        "changeset": 44404040,
        "user": "a mapper",
    }


def way(way_id: int, node_ids: list[int], **tags: str) -> dict[str, Any]:
    return {
        "type": "way",
        "id": way_id,
        "version": 7,
        "nodes": node_ids,
        "tags": tags or {"highway": "path"},
    }


def member(kind: str, ref: int, role: str = "") -> dict[str, Any]:
    return {"type": kind, "ref": ref, "role": role}


def relation(
    relation_id: int, members: list[dict[str, Any]], **tags: str
) -> dict[str, Any]:
    return {
        "type": "relation",
        "id": relation_id,
        "version": 12,
        "members": members,
        "tags": {"type": "route", "route": "hiking", **tags},
    }


def payload(*elements: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": "0.6",
        "generator": "CGImap",
        "copyright": "OpenStreetMap and contributors",
        "elements": list(elements),
    }


#: The route: 1-2-3, then 3-4-5, then 7-6-5 — the last mapped backwards, as
#: ways in a real relation routinely are.
WAYS = [way(100, [1, 2, 3]), way(101, [3, 4, 5]), way(102, [7, 6, 5])]

ROUTE_TAGS = {
    "name": "נחל גלים",
    "name:he": "נחל גלים",
    "name:en": "Nahal Galim",
    "description": "A circular route up the wadi",
    "distance": "6.5",
    "ascent": "256",
    "descent": "251",
}

WHOLE_ROUTE = payload(
    *(node(node_id) for node_id in NODES),
    *WAYS,
    relation(
        RELATION,
        [member("way", 100), member("way", 101), member("way", 102), member("node", 1)],
        **ROUTE_TAGS,
    ),
)

#: What the ways above add up to, in GeoJSON order.
WHOLE_LINE = [[lng, lat] for lat, lng in NODES.values()]


@pytest.fixture
def osm() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=OSM_URL, assert_all_called=False) as router:
        yield router


def serve(
    router: respx.MockRouter, body: dict[str, Any], *, path: str | None = None
) -> respx.Route:
    return router.get(path or f"/relation/{RELATION}/full.json").mock(
        return_value=httpx.Response(200, json=body)
    )


@pytest.fixture
def osm_route(osm: respx.MockRouter) -> respx.Route:
    return serve(osm, WHOLE_ROUTE)


async def call(ref: dict[str, str] | None = None, **arguments: Any) -> dict[str, Any]:
    async with connected_session() as session:
        result = await session.call_tool(
            "get_route_details", {"route": ref or OSM_REF, **arguments}
        )

    assert result.isError is False, result.content
    assert result.structuredContent is not None
    return result.structuredContent


async def call_expecting_error(ref: dict[str, str] | None = None, **arguments: Any) -> str:
    async with connected_session() as session:
        result = await session.call_tool(
            "get_route_details", {"route": ref or OSM_REF, **arguments}
        )

    assert result.isError is True
    return "\n".join(getattr(block, "text", "") for block in result.content)


# --- what comes back ---------------------------------------------------------


async def test_a_relations_ways_are_joined_into_one_line_in_walking_order(
    osm_route: respx.Route,
):
    result = await call()

    assert result["geometry"]["type"] == "LineString"
    assert result["geometry"]["coordinates"] == WHOLE_LINE
    assert result["startPoint"] == {"lat": 32.75749, "lng": 35.02111}
    assert result["geometryDetail"]["recordedPointCount"] == len(NODES)


async def test_the_tags_the_mappers_wrote_are_what_the_answer_reports(
    osm_route: respx.Route,
):
    result = await call(language="en")

    assert result["ref"] == OSM_REF
    assert result["title"] == "Nahal Galim"
    assert result["description"] == "A circular route up the wadi"
    assert result["activity"] == "Hiking"
    assert result["lengthKm"] == 6.5
    assert result["ascentMeters"] == 256
    assert result["descentMeters"] == 251
    assert result["ihmUrl"] == f"{BASE_URL}/poi/OSM/relation_{RELATION}?language=en"
    assert "OpenStreetMap" in " ".join(result["attribution"]["sources"])


async def test_the_requested_language_picks_the_name(osm_route: respx.Route):
    result = await call(language="he")

    assert result["title"] == "נחל גלים"
    assert result["ihmUrl"].endswith("?language=he")


async def test_the_answer_says_who_mapped_the_route_and_what_it_cannot_say(
    osm_route: respx.Route,
):
    result = await call()

    assert "mapped in OpenStreetMap by volunteers" in " ".join(result["warnings"])
    assert "open, permitted or passable" in " ".join(result["unknowns"])


async def test_geometry_is_fetched_from_openstreetmap_with_this_servers_name(
    osm_route: respx.Route,
):
    """A second host, a second usage policy: OSM asks to know who is calling."""
    await call()

    request = osm_route.calls.last.request
    assert request.url.host == "api.openstreetmap.org"
    assert request.url.path == f"/api/0.6/relation/{RELATION}/full.json"
    assert "israel-hiking-mcp/" in request.headers["user-agent"]


async def test_a_single_way_resolves_without_a_relation(osm: respx.MockRouter):
    """Some routes are mapped as one way rather than as a relation."""
    serve(
        osm,
        payload(*(node(node_id) for node_id in (1, 2, 3)), way(100, [1, 2, 3])),
        path="/way/100/full.json",
    )

    result = await call({"source": "OSM", "identifier": "way_100"})

    assert result["geometry"]["coordinates"] == WHOLE_LINE[:3]
    assert result["title"] == "OpenStreetMap way 100"


async def test_a_route_with_no_name_is_titled_by_its_identity(osm: respx.MockRouter):
    serve(
        osm,
        payload(
            *(node(node_id) for node_id in (1, 2, 3)),
            way(100, [1, 2, 3]),
            relation(RELATION, [member("way", 100)]),
        ),
    )

    result = await call()

    assert result["title"] == f"OpenStreetMap relation {RELATION}"
    assert result["description"] is None
    assert result["lengthKm"] is None
    assert result["ascentMeters"] is None
    assert result["difficulty"] is None


async def test_ways_that_do_not_meet_stay_separate_lines_and_are_reported(
    osm: respx.MockRouter,
):
    serve(
        osm,
        payload(
            *(node(node_id) for node_id in [1, 2, 3, *ELSEWHERE]),
            way(100, [1, 2, 3]),
            way(103, [8, 9]),
            relation(RELATION, [member("way", 100), member("way", 103)]),
        ),
    )

    result = await call()

    assert result["geometry"]["type"] == "MultiLineString"
    assert [len(line) for line in result["geometry"]["coordinates"]] == [3, 2]
    assert "2 separate lines that do not meet" in " ".join(result["warnings"])


async def test_a_way_listed_twice_is_drawn_once(osm: respx.MockRouter):
    """Out and back over the same path is one path, mapped once."""
    serve(
        osm,
        payload(
            *(node(node_id) for node_id in (1, 2, 3)),
            way(100, [1, 2, 3]),
            relation(RELATION, [member("way", 100), member("way", 100, "backward")]),
        ),
    )

    result = await call()

    assert result["geometry"]["coordinates"] == WHOLE_LINE[:3]


async def test_a_way_missing_a_node_is_left_out_rather_than_drawn_with_a_gap(
    osm: respx.MockRouter,
):
    """`/full` promises every node; a line closed over a missing one would
    claim the route passes somewhere it does not."""
    serve(
        osm,
        payload(
            *(node(node_id) for node_id in (1, 2, 3, 8, 9)),
            way(100, [1, 2, 3]),
            way(103, [8, 9, 404]),
            relation(RELATION, [member("way", 100), member("way", 103)]),
        ),
    )

    result = await call()

    assert result["geometry"]["type"] == "LineString"
    assert result["geometry"]["coordinates"] == WHOLE_LINE[:3]


async def test_a_long_route_is_thinned_without_pointing_at_a_length_it_lacks(
    osm: respx.MockRouter,
):
    """A mapped trail runs to tens of thousands of positions, and almost none
    of them carry a `distance` tag to compare the thinned line against."""
    walk = [
        {"type": "node", "id": 1_000 + step, "lat": 32.75 + step * 0.00002, "lon": 35.02}
        for step in range(4_000)
    ]
    serve(
        osm,
        payload(
            *walk,
            way(200, [point["id"] for point in walk]),
            relation(RELATION, [member("way", 200)]),
        ),
    )

    result = await call()

    assert result["lengthKm"] is None
    assert result["geometryDetail"]["recordedPointCount"] == 4_000
    assert result["geometryDetail"]["simplified"] is True
    (thinning,) = [warning for warning in result["warnings"] if "thinned" in warning]
    assert "the recorded line" in thinning
    assert "lengthKm" not in thinning


# --- relations inside relations ----------------------------------------------


async def test_a_relation_of_relations_is_followed_and_its_sections_joined(
    osm: respx.MockRouter,
):
    """`/full` returns a nested relation's members but not their geometry, so
    each section costs a request of its own."""
    super_route = serve(
        osm,
        payload(
            relation(9000, [member("relation", RELATION), member("relation", 9001)]),
            relation(RELATION, [member("way", 100), member("way", 101)]),
            relation(9001, [member("way", 102)]),
        ),
        path="/relation/9000/full.json",
    )
    section_one = serve(
        osm,
        payload(
            *(node(node_id) for node_id in (1, 2, 3, 4, 5)),
            way(100, [1, 2, 3]),
            way(101, [3, 4, 5]),
            relation(RELATION, [member("way", 100), member("way", 101)]),
        ),
    )
    section_two = serve(
        osm,
        payload(
            *(node(node_id) for node_id in (5, 6, 7)),
            way(102, [7, 6, 5]),
            relation(9001, [member("way", 102)]),
        ),
        path="/relation/9001/full.json",
    )

    result = await call({"source": "OSM", "identifier": "relation_9000"})

    assert result["geometry"]["coordinates"] == WHOLE_LINE
    assert (super_route.call_count, section_one.call_count, section_two.call_count) == (
        1,
        1,
        1,
    )


async def test_a_relation_that_contains_itself_does_not_loop_forever(
    osm: respx.MockRouter,
):
    """Circular membership is invalid but nothing stops anyone mapping it."""
    first = serve(
        osm,
        payload(
            *(node(node_id) for node_id in (1, 2, 3)),
            way(100, [1, 2, 3]),
            relation(RELATION, [member("way", 100), member("relation", 9001)]),
            relation(9001, [member("relation", RELATION)]),
        ),
    )
    second = serve(
        osm,
        # `/full` of 9001 returns the relation it references as an object, but
        # not that relation's own members' geometry.
        payload(
            relation(9001, [member("relation", RELATION)]),
            relation(RELATION, [member("way", 100), member("relation", 9001)]),
        ),
        path="/relation/9001/full.json",
    )

    result = await call()

    assert result["geometry"]["coordinates"] == WHOLE_LINE[:3]
    # Each relation is fetched once, however many times it is referenced.
    assert (first.call_count, second.call_count) == (1, 1)


async def test_an_endless_chain_of_relations_is_refused_at_the_request_budget(
    osm: respx.MockRouter,
):
    """A route nested deeper than the budget is refused, not half-answered:
    part of a trail returned as the whole trail is the dangerous answer."""

    def section(request: httpx.Request) -> httpx.Response:
        relation_id = int(request.url.path.split("/")[-2])
        return httpx.Response(
            200,
            json=payload(
                relation(relation_id, [member("relation", relation_id + 1)]),
                relation(relation_id + 1, [member("relation", relation_id + 2)]),
            ),
        )

    endless = osm.get(path__regex=r"/relation/\d+/full\.json").mock(side_effect=section)

    text = await call_expecting_error({"source": "OSM", "identifier": "relation_9000"})

    assert "[geometry_too_large]" in text
    assert endless.call_count == settings.max_osm_requests_per_tool_call


# --- refs and answers this server will not turn into a route -----------------


async def test_a_node_is_refused_without_asking_openstreetmap(osm: respx.MockRouter):
    """A point cannot be a route, so the request is not worth OSM's time."""
    anything = osm.get(path__regex=r".*")

    text = await call_expecting_error({"source": "OSM", "identifier": "node_1234"})

    assert "[invalid_input]" in text
    assert "not a route" in text
    assert anything.call_count == 0


@pytest.mark.parametrize(
    "identifier",
    ["282071", "relation-282071", "relation_", "relation_abc", "relation_0", "way_-3"],
)
async def test_an_identifier_this_server_cannot_read_is_refused_not_guessed_at(
    osm: respx.MockRouter, identifier: str
):
    anything = osm.get(path__regex=r".*")

    text = await call_expecting_error({"source": "OSM", "identifier": identifier})

    assert "[invalid_input]" in text
    assert "relation_282071" in text  # the message shows the form expected
    assert anything.call_count == 0


@pytest.mark.parametrize(
    "identifier", [f"  relation_{RELATION} ", f"RELATION_{RELATION}"]
)
async def test_an_identifier_is_read_past_spacing_and_case(
    osm_route: respx.Route, identifier: str
):
    result = await call({"source": "OSM", "identifier": identifier})

    assert result["geometry"]["coordinates"] == WHOLE_LINE


@pytest.mark.parametrize("status", [404, 410])
async def test_a_relation_that_is_gone_is_route_not_found(
    osm: respx.MockRouter, status: int
):
    """OSM answers 410 for something deleted, 404 for something never there."""
    osm.get(path__startswith="/relation/").mock(return_value=httpx.Response(status))

    text = await call_expecting_error()

    assert "[route_not_found]" in text
    assert f"relation {RELATION}" in text


async def test_a_relation_that_draws_no_line_is_route_not_found(osm: respx.MockRouter):
    """A relation of nothing but nodes is a collection of points."""
    serve(
        osm,
        payload(
            node(1),
            relation(RELATION, [member("node", 1)]),
        ),
    )

    text = await call_expecting_error()

    assert "[route_not_found]" in text
    assert "draws no line" in text


async def test_an_answer_without_elements_is_not_guessed_at(osm: respx.MockRouter):
    serve(osm, {"version": "0.6", "generator": "CGImap"})

    text = await call_expecting_error()

    assert "[upstream_schema_changed]" in text


async def test_an_answer_that_leaves_out_the_route_asked_for_is_not_guessed_at(
    osm: respx.MockRouter,
):
    serve(osm, payload(node(1), way(100, [1, 2, 3])))

    text = await call_expecting_error()

    assert "[upstream_schema_changed]" in text
    assert f"relation {RELATION}" in text


async def test_an_outage_at_openstreetmap_keeps_its_own_code(osm: respx.MockRouter):
    osm.get(path__startswith="/relation/").mock(return_value=httpx.Response(503))

    text = await call_expecting_error()

    assert "[upstream_unavailable]" in text
    assert "api.openstreetmap.org" in text


# --- the pieces on their own -------------------------------------------------


def test_an_identifier_becomes_a_type_and_an_id():
    feature = OsmFeature.parse("relation_282071")

    assert (feature.kind, feature.osmId) == ("relation", 282071)
    assert feature.path == "relation/282071/full.json"
    assert OsmFeature.parse("node_5").path == "node/5.json"


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        # Nothing to join.
        ([[(35.0, 32.0), (35.1, 32.1)]], [[(35.0, 32.0), (35.1, 32.1)]]),
        # End to start: the shared position is kept once.
        (
            [[(35.0, 32.0), (35.1, 32.1)], [(35.1, 32.1), (35.2, 32.2)]],
            [[(35.0, 32.0), (35.1, 32.1), (35.2, 32.2)]],
        ),
        # End to end: the second way was mapped backwards.
        (
            [[(35.0, 32.0), (35.1, 32.1)], [(35.2, 32.2), (35.1, 32.1)]],
            [[(35.0, 32.0), (35.1, 32.1), (35.2, 32.2)]],
        ),
        # Start to end: the second way comes before the first.
        (
            [[(35.1, 32.1), (35.2, 32.2)], [(35.0, 32.0), (35.1, 32.1)]],
            [[(35.0, 32.0), (35.1, 32.1), (35.2, 32.2)]],
        ),
        # Start to start: the first way was mapped backwards.
        (
            [[(35.1, 32.1), (35.2, 32.2)], [(35.1, 32.1), (35.0, 32.0)]],
            [[(35.0, 32.0), (35.1, 32.1), (35.2, 32.2)]],
        ),
        # A line arriving last can be what closes two chains into one.
        (
            [
                [(35.0, 32.0), (35.1, 32.1)],
                [(35.2, 32.2), (35.3, 32.3)],
                [(35.1, 32.1), (35.2, 32.2)],
            ],
            [[(35.0, 32.0), (35.1, 32.1), (35.2, 32.2), (35.3, 32.3)]],
        ),
        # Lines that share no end are two lines, and stay two.
        (
            [[(35.0, 32.0), (35.1, 32.1)], [(34.0, 31.0), (34.1, 31.1)]],
            [[(35.0, 32.0), (35.1, 32.1)], [(34.0, 31.0), (34.1, 31.1)]],
        ),
        # A line of one position draws nothing and joins nothing.
        ([[(35.0, 32.0)]], []),
    ],
)
def test_lines_are_joined_where_their_ends_meet(
    lines: list[list[tuple[float, float]]], expected: list[list[tuple[float, float]]]
):
    assert merge_lines(lines) == expected


def test_a_closed_loop_stays_one_line():
    """A circular walk ends where it started, and must not be mistaken for two
    lines that happen to touch."""
    ring = [(35.0, 32.0), (35.1, 32.0), (35.1, 32.1), (35.0, 32.0)]

    assert merge_lines([ring[:2], ring[1:]]) == [ring]


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"route": "hiking"}, "Hiking"),
        ({"route": "foot"}, "Hiking"),
        ({"route": "mtb"}, "Bicycle"),
        ({"route": "bicycle"}, "Bicycle"),
        ({"route": "road", "scenic": "yes"}, "4x4"),
        ({"route": "road"}, "road"),
        ({"route": "ski"}, "ski"),
        ({"highway": "path"}, None),
    ],
)
def test_the_activity_is_the_route_tag_in_the_map_sites_words(
    tags: dict[str, str], expected: str | None
):
    assert osm_activity(tags) == expected


@pytest.mark.parametrize(
    ("stated", "expected"),
    [("hard", "Hard"), ("Very hard", "Very Hard"), ("T4", None), ("", None)],
)
def test_a_difficulty_tag_counts_only_where_it_names_a_known_grade(
    stated: str, expected: str | None
):
    assert osm_difficulty({"difficulty": stated}) == expected


@pytest.mark.parametrize(
    ("stated", "expected"),
    [
        ("6.5", 6.5),
        ("6.5 km", 6.5),
        ("80", 80.0),
        ("0", None),
        ("40 mi", None),
        ("", None),
    ],
)
def test_the_distance_tag_is_read_as_kilometres_or_not_at_all(
    stated: str, expected: float | None
):
    assert osm_length_km({"distance": stated}) == expected
