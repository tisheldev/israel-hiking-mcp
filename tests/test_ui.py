"""The MCP Apps contract: one versioned resource, and the tools that point at it.

What is checked here is the protocol and the packaging, not the map. Whether a
line is drawn in the right place is the frontend's own test suite (`ui/src`);
what matters on this side is that a host is offered exactly one trail-map
document, that it arrives with the MIME type and the CSP that let a host frame
it safely, that the two geometry tools advertise it and no other tool does —
and, above all, that a host which knows nothing about MCP Apps still gets the
whole answer.

That last one is why this file exists separately from `test_protocol.py`: the
general MCP contract belongs there, and this feature's job is to add nothing to
it that a client is required to understand.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import httpx
import pytest
import respx
from mcp.types import TextContent, TextResourceContents
from pydantic import AnyUrl

from ihm_mcp.config import get_settings
from ihm_mcp.ui import (
    TILE_ORIGIN,
    TRAIL_MAP_MIME_TYPE,
    TRAIL_MAP_RESOURCE_URI,
    trail_map_html,
)
from tests.conftest import connected_session

BASE_URL = str(get_settings().base_url).rstrip("/")

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The tools whose results carry a complete route line, and so a map.
GEOMETRY_TOOLS = ["get_route_details", "route_between_points"]

#: The tools that must not advertise one. A search result holds start markers,
#: not routes; a POI result holds points beside a route somebody already has.
#: Attaching a map to either would draw something the result does not contain.
NON_GEOMETRY_TOOLS = ["find_pois_along_route", "search_hiking_routes", "search_places"]


# --- the resource ------------------------------------------------------------


async def test_exactly_one_trail_map_resource_is_offered():
    """One document, named with its version.

    More than one would mean an old build was left in `assets/`; a host would
    have no way to tell which is current.
    """
    async with connected_session() as session:
        resources = (await session.list_resources()).resources

    trail_maps = [r for r in resources if str(r.uri).startswith("ui://israel-hiking/")]
    assert len(trail_maps) == 1

    (trail_map,) = trail_maps
    assert str(trail_map.uri) == TRAIL_MAP_RESOURCE_URI
    assert re.search(r"-v\d+\.html$", str(trail_map.uri)), "the URI must carry a version"
    assert trail_map.mimeType == TRAIL_MAP_MIME_TYPE


async def test_the_listed_resource_carries_its_framing_metadata():
    """`prefersBorder` and a CSP narrow enough to be worth declaring."""
    async with connected_session() as session:
        resources = (await session.list_resources()).resources

    (trail_map,) = [r for r in resources if str(r.uri) == TRAIL_MAP_RESOURCE_URI]

    assert trail_map.meta is not None
    ui_meta = trail_map.meta["ui"]
    assert ui_meta["prefersBorder"] is True
    # Only the tile origin, and only as a static-asset origin: the document
    # fetches nothing and frames nothing.
    assert ui_meta["csp"] == {"resourceDomains": [TILE_ORIGIN]}


async def test_reading_the_resource_returns_the_packaged_document():
    async with connected_session() as session:
        result = await session.read_resource(AnyUrl(TRAIL_MAP_RESOURCE_URI))

    (contents,) = result.contents
    assert isinstance(contents, TextResourceContents)
    assert str(contents.uri) == TRAIL_MAP_RESOURCE_URI
    assert contents.mimeType == TRAIL_MAP_MIME_TYPE
    assert contents.text == trail_map_html()
    assert contents.text.lstrip().lower().startswith("<!doctype html")


async def test_the_read_resource_repeats_its_csp():
    """A host that reads without listing still learns what to permit."""
    async with connected_session() as session:
        result = await session.read_resource(AnyUrl(TRAIL_MAP_RESOURCE_URI))

    (contents,) = result.contents
    assert contents.meta is not None
    assert contents.meta["ui"]["csp"]["resourceDomains"] == [TILE_ORIGIN]


# --- what the tools advertise -------------------------------------------------


async def test_the_geometry_tools_advertise_the_map():
    async with connected_session() as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}

    for name in GEOMETRY_TOOLS:
        meta = tools[name].meta
        assert meta is not None, f"{name} should advertise the trail map"
        assert meta["ui"]["resourceUri"] == TRAIL_MAP_RESOURCE_URI


async def test_no_other_tool_advertises_a_ui_resource():
    async with connected_session() as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}

    for name in NON_GEOMETRY_TOOLS:
        meta = tools[name].meta or {}
        assert "ui" not in meta, f"{name} has no complete route line to draw"


async def test_the_two_tools_share_one_resource():
    """One document, not one per tool — and neither mutates the other's meta."""
    async with connected_session() as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}

    metas = [tools[name].meta for name in GEOMETRY_TOOLS]
    assert all(meta is not None for meta in metas)
    advertised = {meta["ui"]["resourceUri"] for meta in metas if meta is not None}
    assert advertised == {TRAIL_MAP_RESOURCE_URI}


# --- graceful degradation -----------------------------------------------------


@pytest.fixture
def one_place():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get(path__startswith="/api/search/").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "node_29429711",
                        "source": "OSM",
                        "title": "Haifa",
                        "displayName": "Haifa, Haifa Subdistrict, Israel",
                        "location": {"lat": 32.8191218, "lng": 34.9983856},
                        "hasExtraData": True,
                    }
                ],
            )
        )
        yield router


async def test_a_client_that_knows_nothing_of_mcp_apps_still_gets_everything(
    one_place: respx.MockRouter,
):
    """The test client declares no UI capability — as most hosts do.

    It must still receive the structured result and the text mirror, unchanged.
    The map is an enhancement; nothing in an answer may depend on it.
    """
    async with connected_session() as session:
        result = await session.call_tool("search_places", {"query": "Haifa"})

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["places"][0]["title"] == "Haifa"

    (block,) = result.content
    assert isinstance(block, TextContent)
    assert block.text


async def test_advertising_a_ui_resource_does_not_change_a_tool_schema():
    """The geometry tools' schemas are what they were before the map existed."""
    async with connected_session() as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}

    routing = tools["route_between_points"]
    assert sorted(routing.inputSchema["required"]) == ["end", "start"]
    assert routing.inputSchema["properties"]["activity"]["default"] == "Hiking"
    assert routing.annotations is not None
    assert routing.annotations.readOnlyHint is True

    details = tools["get_route_details"]
    assert sorted(details.inputSchema["required"]) == ["route"]
    assert details.outputSchema is not None


# --- the packaged document ----------------------------------------------------


def test_the_document_loads_no_remote_script_or_stylesheet():
    """Everything is inline, which is what makes the narrow CSP honest.

    A single external script or stylesheet would need its origin permitted, and
    would put the component at the mercy of a CDN staying up.
    """
    html = trail_map_html()

    for script in re.findall(r"<script\b[^>]*>", html, flags=re.IGNORECASE):
        assert "src=" not in script.lower(), f"remote script: {script}"

    for link in re.findall(r"<link\b[^>]*>", html, flags=re.IGNORECASE):
        assert "stylesheet" not in link.lower(), f"remote stylesheet: {link}"

    assert "<iframe" not in html.lower()


def test_the_document_requests_tiles_only_from_the_declared_origin():
    """Every origin the document can reach is one the resource's CSP declares."""
    html = trail_map_html()

    assert TILE_ORIGIN in html, "the basemap origin should be in the document"

    # Origins reachable by markup that *fetches*. Anchors are excluded on
    # purpose: attribution links are text a reader may follow, not requests the
    # document makes.
    fetching = re.findall(
        r"<(?:img|script|link|iframe|source)\b[^>]*?(https?://[^\"'\s>]+)",
        html,
        flags=re.IGNORECASE,
    )
    for url in fetching:
        assert url.startswith(TILE_ORIGIN), f"undeclared origin: {url}"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is needed to build a wheel")
def test_the_built_wheel_carries_the_document(tmp_path: Path):
    """Runtime installation stays Python-only, so the artifact must ship."""
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )

    (wheel,) = tmp_path.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()

    assert "ihm_mcp/assets/trail-map-v1.html" in names
