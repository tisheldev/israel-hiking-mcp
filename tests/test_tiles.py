"""Covering an area with tiles, and reading the features back out of them.

No upstream tile data is committed here — the map's data is CC BY-NC-SA and a
captured tile would be a redistribution. Every fixture below is a tile this
test builds with `mapbox-vector-tile`'s own encoder, so the decoding tests
prove this server handles the format, and nothing about what upstream puts in
it. The two conventions they lean on — the OSM identity packed into the feature
id, and `poiGeolocation` arriving as a JSON string — are transcribed from the
map site's `poi.service.ts`, which is the only place they are written down.
"""

from collections.abc import Iterator
from typing import Any

import httpx
import mapbox_vector_tile
import mercantile
import pytest
import respx

from ihm_mcp.config import Settings
from ihm_mcp.errors import (
    InvalidInputError,
    SearchAreaTooLargeError,
    UpstreamSchemaChangedError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from ihm_mcp.ihm_client import UpstreamClient
from ihm_mcp.models import Coordinates
from ihm_mcp.tiles import (
    DEFAULT_ZOOM,
    MAX_ZOOM,
    MIN_ZOOM,
    Tile,
    bounding_box,
    covering_tiles,
    decode_tile,
    layer_points,
    osm_identifier,
    points_in_radius,
    points_in_tiles,
    stated_location,
    tile_path,
    tiles_for_radius,
)

BASE_URL = "https://upstream.test"
BUDGET = 100

HAIFA = Coordinates(lat=32.8191, lng=34.9984)
#: The zoom-12 tile Haifa falls in, from the slippy-map formula.
HAIFA_TILE = mercantile.Tile(x=2446, y=1652, z=12)


# --- building a tile ---------------------------------------------------------


def tile_bytes(layers: dict[str, list[dict[str, Any]]], *, extent: int = 4096) -> bytes:
    """A real MVT body, written the way the spec (and upstream) writes one:
    tile-local integers whose origin is the top-left corner."""
    return mapbox_vector_tile.encode(
        [{"name": name, "features": features} for name, features in layers.items()],
        default_options={"y_coord_down": True, "extents": extent},
    )


def feature(
    geometry: dict[str, Any] | None = None,
    *,
    id: int | None = None,
    at: tuple[int, int] = (2048, 1024),
    **properties: Any,
) -> dict[str, Any]:
    return {
        "geometry": geometry or {"type": "Point", "coordinates": list(at)},
        "properties": properties,
        "id": id,
    }


def pixels(tile: Tile, point: Coordinates, *, extent: int = 4096) -> tuple[int, int]:
    """Where a real coordinate sits on a tile's grid.

    The forward direction of what `tiles.projector` undoes, written through
    mercantile's point projection rather than its tile bounds so the two are
    not the same arithmetic read twice.
    """
    left, bottom, right, top = mercantile.xy_bounds(tile)
    x, y = mercantile.xy(point.lng, point.lat)
    return (
        round((x - left) / (right - left) * extent),
        round((top - y) / (top - bottom) * extent),
    )


def refs(points: list) -> list[str]:
    return [point.ref.identifier for point in points]


# --- covering an area --------------------------------------------------------


def test_a_tiny_radius_needs_only_the_tile_the_point_is_on():
    assert tiles_for_radius(HAIFA, 0.2, max_tiles=BUDGET) == [HAIFA_TILE]
    assert mercantile.tile(HAIFA.lng, HAIFA.lat, DEFAULT_ZOOM) == HAIFA_TILE


def test_tiles_come_back_in_a_stable_order():
    """Tile order decides which copy of an edge-straddling feature is kept, so
    the same search has to produce the same sequence every time."""
    tiles = tiles_for_radius(HAIFA, 12, max_tiles=BUDGET)

    assert tiles == sorted(tiles, key=lambda tile: (tile.x, tile.y))
    assert tiles == tiles_for_radius(HAIFA, 12, max_tiles=BUDGET)


@pytest.mark.parametrize(
    ("radius_km", "expected"), [(1, 1), (5, 4), (10, 10), (20, 30), (40, 98)]
)
def test_what_a_radius_costs_at_the_default_zoom(radius_km: float, expected: int):
    """The numbers this server's radius limits are chosen against."""
    assert len(tiles_for_radius(HAIFA, radius_km, max_tiles=BUDGET)) == expected


def test_the_documented_maximum_radius_fits_the_default_budget():
    """40 km at zoom 12 is what PR 5's `radiusKm` bound promises; the box around
    that circle needs 121 tiles, which is why the circle is what gets covered."""
    assert len(tiles_for_radius(HAIFA, 40, max_tiles=BUDGET)) <= BUDGET
    assert len(covering_tiles(bounding_box(HAIFA, 40), DEFAULT_ZOOM)) == 121


def test_only_tiles_that_could_hold_a_result_are_fetched():
    radius_km = 25
    box = set(covering_tiles(bounding_box(HAIFA, radius_km), DEFAULT_ZOOM))
    disc = set(tiles_for_radius(HAIFA, radius_km, max_tiles=BUDGET))

    assert disc < box
    for dropped in box - disc:
        # Nothing dropped comes within the radius: its nearest corner is further
        # away than anything the caller asked about.
        bounds = mercantile.bounds(dropped)
        corners = [
            Coordinates(lat=lat, lng=lng)
            for lat in (bounds.south, bounds.north)
            for lng in (bounds.west, bounds.east)
        ]
        assert min(haversine_km(HAIFA, corner) for corner in corners) > radius_km


def haversine_km(origin: Coordinates, point: Coordinates) -> float:
    """A real great-circle distance, to check the flat-earth one this server
    uses to pick tiles is not quietly dropping ground it should cover."""
    from math import asin, cos, radians, sin, sqrt

    lat1, lat2 = radians(origin.lat), radians(point.lat)
    d_lat, d_lng = lat2 - lat1, radians(point.lng - origin.lng)
    inner = sin(d_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(d_lng / 2) ** 2
    return 2 * 6371.0088 * asin(sqrt(inner))


@pytest.mark.parametrize("radius_km", [0, -1, -0.0001])
def test_a_radius_that_covers_nothing_is_rejected(radius_km: float):
    with pytest.raises(InvalidInputError):
        tiles_for_radius(HAIFA, radius_km, max_tiles=BUDGET)


@pytest.mark.parametrize("zoom", [0, MIN_ZOOM - 1, MAX_ZOOM + 1, 22])
def test_a_zoom_the_tileset_does_not_publish_is_rejected(zoom: int):
    with pytest.raises(InvalidInputError) as failure:
        tiles_for_radius(HAIFA, 5, zoom=zoom, max_tiles=BUDGET)

    assert f"{MIN_ZOOM}–{MAX_ZOOM}" in str(failure.value)


# --- the budget --------------------------------------------------------------


def test_too_large_an_area_says_how_many_tiles_and_how_many_are_allowed():
    with pytest.raises(SearchAreaTooLargeError) as failure:
        tiles_for_radius(HAIFA, 40, zoom=14, max_tiles=BUDGET)

    message = str(failure.value)
    assert "[search_area_too_large]" in message
    assert "1292 map tiles" in message
    assert f"at most {BUDGET}" in message


def test_the_radius_it_suggests_instead_actually_fits():
    """A suggestion that is itself over budget would send a caller in circles."""
    with pytest.raises(SearchAreaTooLargeError) as failure:
        tiles_for_radius(HAIFA, 40, zoom=14, max_tiles=BUDGET)

    suggested = float(str(failure.value).split("About ")[1].split(" km")[0])
    assert len(tiles_for_radius(HAIFA, suggested, zoom=14, max_tiles=BUDGET)) <= BUDGET


# --- reading one tile --------------------------------------------------------


def test_a_feature_comes_back_where_the_tile_put_it():
    """The whole point of the grid arithmetic: a position written into a tile
    at zoom 12 comes back within a few metres of where it went in."""
    where = Coordinates(lat=32.7900, lng=34.9700)
    blob = tile_bytes(
        {"global_points": [feature(id=2820712, at=pixels(HAIFA_TILE, where))]}
    )

    (point,) = decode_tile(HAIFA_TILE, blob)

    assert point.coordinates.lat == pytest.approx(where.lat, abs=1e-4)
    assert point.coordinates.lng == pytest.approx(where.lng, abs=1e-4)


def test_the_grid_is_read_with_north_at_the_top():
    """A flipped y axis would mirror every feature about the tile's middle and
    still look plausible, so this pins the orientation."""
    north = Coordinates(lat=32.8400, lng=34.9800)
    south = Coordinates(lat=32.7800, lng=34.9800)
    blob = tile_bytes(
        {
            "global_points": [
                feature(id=11, at=pixels(HAIFA_TILE, north)),
                feature(id=21, at=pixels(HAIFA_TILE, south)),
            ]
        }
    )

    by_ref = {point.ref.identifier: point for point in decode_tile(HAIFA_TILE, blob)}

    assert by_ref["node_1"].coordinates.lat > by_ref["node_2"].coordinates.lat
    assert by_ref["node_1"].coordinates.lat == pytest.approx(north.lat, abs=1e-4)


def test_a_tile_with_a_smaller_grid_is_still_read_correctly():
    """`extent` is per layer and upstream is free to change it."""
    where = Coordinates(lat=32.8000, lng=34.9900)
    blob = tile_bytes(
        {"global_points": [feature(id=31, at=pixels(HAIFA_TILE, where, extent=1024))]},
        extent=1024,
    )

    (point,) = decode_tile(HAIFA_TILE, blob)

    assert point.coordinates.lat == pytest.approx(where.lat, abs=1e-3)
    assert point.coordinates.lng == pytest.approx(where.lng, abs=1e-3)


def test_both_source_layers_are_read_and_say_which_they_came_from():
    blob = tile_bytes(
        {
            "global_points": [feature(id=11, poiCategory="Hiking")],
            "external": [feature(id=21, identifier="1234", poiSource="Nakeb")],
        }
    )

    points = decode_tile(HAIFA_TILE, blob)

    assert [point.layer for point in points] == ["global_points", "external"]
    assert points[1].ref.source == "Nakeb"


def test_a_layer_this_server_does_not_know_about_is_left_alone():
    blob = tile_bytes({"global_points": [feature(id=11)], "contours": [feature(id=21)]})

    assert refs(decode_tile(HAIFA_TILE, blob)) == ["node_1"]


def test_upstream_properties_are_passed_through_untouched():
    """The tools downstream read `poiCategory`, `poiLength` and the name fields
    out of this bag; editing it here would be editing their input."""
    blob = tile_bytes(
        {
            "global_points": [
                feature(
                    id=11,
                    poiCategory="Hiking",
                    poiLength=8123.5,
                    poiDifficulty="Moderate",
                    **{"name:he": "שביל"},
                )
            ]
        }
    )

    (point,) = decode_tile(HAIFA_TILE, blob)

    assert point.properties["poiCategory"] == "Hiking"
    assert point.properties["poiLength"] == pytest.approx(8123.5)
    assert point.properties["name:he"] == "שביל"


def test_an_empty_body_is_a_tile_with_nothing_in_it():
    assert decode_tile(HAIFA_TILE, b"") == []


@pytest.mark.parametrize(
    "blob",
    [
        pytest.param(b"not a vector tile at all", id="prose"),
        pytest.param(b"\x1a\x05", id="truncated"),
        pytest.param(bytes(range(64)), id="binary junk"),
    ],
)
def test_a_body_that_is_not_a_tile_is_reported_not_guessed_at(blob: bytes):
    with pytest.raises(UpstreamSchemaChangedError):
        decode_tile(HAIFA_TILE, blob)


def test_a_layer_with_no_grid_size_cannot_be_placed_and_says_so():
    """Nothing in the encoder can produce this; a changed upstream could."""
    with pytest.raises(UpstreamSchemaChangedError) as failure:
        layer_points(HAIFA_TILE, "global_points", {"features": [feature(id=11)]})

    assert "extent" in str(failure.value)


# --- who a feature is --------------------------------------------------------


@pytest.mark.parametrize(
    ("feature_id", "expected"),
    [
        (2820711, "node_282071"),
        (2820712, "way_282071"),
        (2820713, "relation_282071"),
        (2820719, "relation_282071"),
        (2820710, "relation_282071"),
        (11, "node_1"),
    ],
)
def test_an_osm_identity_is_unpacked_from_the_feature_id(feature_id: int, expected: str):
    """The last digit is the type and the rest is the id — upstream's own trick,
    and the only thing in a tile that says what a feature actually is."""
    assert osm_identifier(feature_id) == expected


@pytest.mark.parametrize("feature_id", [None, 0, -11, "way_1", 1.5, True])
def test_an_id_that_cannot_be_unpacked_is_refused(feature_id: Any):
    assert osm_identifier(feature_id) is None


def test_an_identifier_property_is_believed_over_the_packed_id():
    blob = tile_bytes({"global_points": [feature(id=999992, identifier="relation_42")]})

    assert refs(decode_tile(HAIFA_TILE, blob)) == ["relation_42"]


def test_a_feature_with_no_way_to_name_it_is_dropped_not_invented():
    """Without an identity nothing downstream could link to it or ask about it,
    and one nameless marker is not a reason to fail a whole search."""
    blob = tile_bytes({"global_points": [feature(id=None), feature(id=11)]})

    assert refs(decode_tile(HAIFA_TILE, blob)) == ["node_1"]


def test_a_feature_that_names_no_dataset_is_treated_as_osm():
    blob = tile_bytes({"global_points": [feature(id=11)]})

    (point,) = decode_tile(HAIFA_TILE, blob)

    assert point.ref.source == "OSM"


# --- where a feature is ------------------------------------------------------


def test_the_stated_geolocation_wins_over_the_drawn_marker():
    """Upstream computes `poiGeolocation`; the drawn point is snapped to the
    tile grid. MVT cannot carry an object, so it arrives as a JSON string."""
    stated = {"lat": 32.7654, "lng": 34.9321}
    blob = tile_bytes(
        {
            "global_points": [
                feature(id=11, at=(10, 10), poiGeolocation='{"lat": 32.7654, "lng": 34.9321}')
            ]
        }
    )

    (point,) = decode_tile(HAIFA_TILE, blob)

    assert point.coordinates.lat == stated["lat"]
    assert point.coordinates.lng == stated["lng"]


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param('{"lat": 32.5', id="truncated json"),
        pytest.param('{"lat": 32.5}', id="no lng"),
        pytest.param('{"lat": "north", "lng": 35.0}', id="lat is not a number"),
        pytest.param('{"lat": 932.0, "lng": 35.0}', id="impossible latitude"),
        pytest.param("[32.5, 35.0]", id="an array"),
        pytest.param("", id="empty"),
    ],
)
def test_an_unusable_geolocation_falls_back_to_the_geometry(raw: str):
    """One odd property is not a reason to lose a feature whose marker is fine."""
    where = Coordinates(lat=32.8000, lng=34.9900)
    blob = tile_bytes(
        {
            "global_points": [
                feature(id=11, at=pixels(HAIFA_TILE, where), poiGeolocation=raw)
            ]
        }
    )

    (point,) = decode_tile(HAIFA_TILE, blob)

    assert point.coordinates.lat == pytest.approx(where.lat, abs=1e-4)


def test_a_geolocation_that_is_already_an_object_is_read_as_one():
    """Tiles send a string; the same property reaches this code as an object
    from other upstream shapes, and both are the same fact."""
    located = stated_location({"poiGeolocation": {"lat": 32.5, "lng": 35.0}})

    assert located == Coordinates(lat=32.5, lng=35.0)


def test_a_line_is_placed_at_its_first_vertex():
    start = Coordinates(lat=32.8300, lng=34.9600)
    end = Coordinates(lat=32.7700, lng=35.0300)
    blob = tile_bytes(
        {
            "global_points": [
                feature(
                    {
                        "type": "LineString",
                        "coordinates": [
                            list(pixels(HAIFA_TILE, start)),
                            list(pixels(HAIFA_TILE, end)),
                        ],
                    },
                    id=11,
                )
            ]
        }
    )

    (point,) = decode_tile(HAIFA_TILE, blob)

    assert point.coordinates.lat == pytest.approx(start.lat, abs=1e-4)
    assert point.coordinates.lng == pytest.approx(start.lng, abs=1e-4)


def test_an_area_is_placed_in_the_middle_of_its_extent():
    south_west = Coordinates(lat=32.7800, lng=34.9600)
    north_east = Coordinates(lat=32.8400, lng=35.0200)
    left, top = pixels(HAIFA_TILE, Coordinates(lat=north_east.lat, lng=south_west.lng))
    right, bottom = pixels(HAIFA_TILE, Coordinates(lat=south_west.lat, lng=north_east.lng))
    blob = tile_bytes(
        {
            "global_points": [
                feature(
                    {
                        "type": "Polygon",
                        "coordinates": [
                            [[left, top], [right, top], [right, bottom], [left, bottom]]
                        ],
                    },
                    id=13,
                )
            ]
        }
    )

    (point,) = decode_tile(HAIFA_TILE, blob)

    assert point.coordinates.lat == pytest.approx(
        (south_west.lat + north_east.lat) / 2, abs=1e-3
    )
    assert point.coordinates.lng == pytest.approx(
        (south_west.lng + north_east.lng) / 2, abs=1e-3
    )


# --- across tiles ------------------------------------------------------------


def make_settings(**overrides: Any) -> Settings:
    values = {
        "base_url": BASE_URL,
        "request_timeout_seconds": 1.0,
        "max_concurrent_requests": 4,
        "max_tiles_per_tool_call": BUDGET,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def client() -> Iterator[UpstreamClient]:
    yield UpstreamClient(make_settings(), retry_backoff_seconds=0)


@pytest.fixture
def mock_upstream() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


def serve(router: respx.MockRouter, bodies: dict[Tile, bytes]) -> respx.Route:
    """Answer each tile with its own body, and empty ground for the rest."""

    def respond(request: httpx.Request) -> httpx.Response:
        for tile, blob in bodies.items():
            if request.url.path == tile_path(tile):
                return httpx.Response(
                    200, content=blob, headers={"content-type": "application/x-protobuf"}
                )
        return httpx.Response(404)

    return router.get(path__startswith="/vector/data/global_points/").mock(
        side_effect=respond
    )


async def test_a_feature_on_a_tile_edge_comes_back_once(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    """A marker near a boundary is written into both neighbouring tiles."""
    neighbour = mercantile.Tile(x=HAIFA_TILE.x + 1, y=HAIFA_TILE.y, z=HAIFA_TILE.z)
    both = tile_bytes({"global_points": [feature(id=2820711, poiCategory="Hiking")]})
    serve(mock_upstream, {HAIFA_TILE: both, neighbour: both})

    points = await points_in_tiles(client, [HAIFA_TILE, neighbour])

    assert refs(points) == ["node_282071"]


async def test_every_tile_of_the_area_is_asked_for(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    route = serve(mock_upstream, {})

    await points_in_radius(client, HAIFA, 10)

    asked = {call.request.url.path for call in route.calls}
    assert asked == {tile_path(tile) for tile in tiles_for_radius(HAIFA, 10, max_tiles=BUDGET)}
    assert len(asked) == 10


async def test_a_tile_the_map_never_cut_is_empty_ground_not_a_failure(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    """Tile servers answer 404 for areas with nothing in them; a search whose
    box touches the sea must not fail on it."""
    serve(mock_upstream, {HAIFA_TILE: tile_bytes({"global_points": [feature(id=11)]})})

    points = await points_in_radius(client, HAIFA, 10)

    assert refs(points) == ["node_1"]


async def test_one_unreadable_tile_fails_the_search_rather_than_leaving_a_hole(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    """Half an area, with nothing in the answer able to say which half, reads
    to a model as "there is nothing there"."""
    tiles = tiles_for_radius(HAIFA, 10, max_tiles=BUDGET)
    good = tile_bytes({"global_points": [feature(id=11)]})

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == tile_path(tiles[-1]):
            return httpx.Response(503)
        return httpx.Response(
            200, content=good, headers={"content-type": "application/x-protobuf"}
        )

    mock_upstream.get(path__startswith="/vector/data/").mock(side_effect=respond)

    with pytest.raises(UpstreamUnavailableError):
        await points_in_radius(client, HAIFA, 10)


async def test_an_undecodable_tile_keeps_its_own_code(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    serve(mock_upstream, {HAIFA_TILE: b"this is not a vector tile"})

    with pytest.raises(UpstreamSchemaChangedError):
        await points_in_tiles(client, [HAIFA_TILE])


async def test_a_timeout_reaches_the_caller_as_a_timeout(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    mock_upstream.get(path__startswith="/vector/data/").mock(side_effect=httpx.ReadTimeout)

    with pytest.raises(UpstreamTimeoutError):
        await points_in_tiles(client, [HAIFA_TILE])


async def test_the_budget_is_the_configured_one(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    serve(mock_upstream, {})
    small = UpstreamClient(make_settings(max_tiles_per_tool_call=4))

    with pytest.raises(SearchAreaTooLargeError) as failure:
        await points_in_radius(small, HAIFA, 10)

    assert "at most 4 per call" in str(failure.value)


async def test_the_same_tile_is_only_fetched_once(
    client: UpstreamClient, mock_upstream: respx.MockRouter
):
    """The client's cache is what keeps two searches over the same area from
    costing twice as much upstream."""
    route = serve(mock_upstream, {HAIFA_TILE: tile_bytes({"global_points": []})})

    await points_in_tiles(client, [HAIFA_TILE])
    await points_in_tiles(client, [HAIFA_TILE])

    assert route.call_count == 1
