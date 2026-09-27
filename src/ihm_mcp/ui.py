"""The inline trail map, as an MCP Apps resource.

This module owns three things and nothing else: the resource URI, the metadata
a host needs to frame the document safely, and reading the packaged HTML off
disk. It holds no rendering logic, and no tool imports anything from here except
`TRAIL_MAP_TOOL_META`.

**The map is progressive enhancement.** A host that does not implement MCP Apps
never reads this resource and loses nothing: `get_route_details` and
`route_between_points` return the same structured result, the same JSON text
mirror, the same warnings, unknowns and attribution as before. Nothing in a
result is explained only by the picture.

**The document is a build artifact.** `ui/src` is its source; the committed
`assets/trail-map-v1.html` is what `npm run build` produces from it, inlined by
`vite-plugin-singlefile` into one file with no external script or stylesheet.
Keeping the built document in the package is what lets the server stay a
Python-and-`uv` install — Node is needed only to change the UI.

**The URI carries the version.** A host may cache a `ui://` document, so a
change to what the component expects from a result must arrive under a new
name rather than quietly under the old one.
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import Any, Final

from ihm_mcp.app import mcp

#: The one name for this component. Tools advertise it, the host reads it, and
#: `assets/` holds the file it resolves to — change it in one place or not at
#: all.
TRAIL_MAP_RESOURCE_URI: Final = "ui://israel-hiking/trail-map-v1.html"

#: What MCP Apps calls an HTML view. The `profile` parameter is what separates
#: a document a host should frame from one it should merely display.
TRAIL_MAP_MIME_TYPE: Final = "text/html;profile=mcp-app"

#: The built document, inside the installed package.
ASSET_PACKAGE: Final = "ihm_mcp"
ASSET_NAME: Final = "assets/trail-map-v1.html"

#: The basemap the document requests tiles from, and the only origin it is
#: allowed to reach. A low-volume raster source, chosen for a personal,
#: non-commercial project; a public deployment should point both this and
#: `TILE_URL` in `ui/src/map.ts` at a provider sized for its traffic.
TILE_ORIGIN: Final = "https://tile.openstreetmap.org"

#: What the host should permit the framed document to do.
#:
#: Only `resourceDomains`, and only the tile origin: the document fetches
#: nothing, embeds no frame, and carries its own script and stylesheet inline,
#: so every other domain list would be a permission nothing uses.
TRAIL_MAP_RESOURCE_META: Final[dict[str, Any]] = {
    "ui": {
        "csp": {"resourceDomains": [TILE_ORIGIN]},
        # A map is a panel with edges, not a run of text in the transcript.
        "prefersBorder": True,
    }
}

#: What a tool attaches to say "render my result with this".
#:
#: One shared mapping, imported by both geometry tools. Copied on read, because
#: a tool decorator that mutated it would change the other tool's metadata too.
TRAIL_MAP_TOOL_META: Final[dict[str, Any]] = {
    "ui": {"resourceUri": TRAIL_MAP_RESOURCE_URI}
}


@cache
def trail_map_html() -> str:
    """The built document, read once per process.

    Through `importlib.resources` rather than `__file__`, so it is found the
    same way whether the package is installed, run from a checkout, or zipped.

    A missing asset is a packaging fault, not a runtime condition: it means the
    wheel was built without `npm run build` having been run. Raising here — at
    the first read — says that plainly, instead of registering a resource that
    serves an empty document.
    """
    asset = files(ASSET_PACKAGE).joinpath(ASSET_NAME)
    if not asset.is_file():
        raise FileNotFoundError(
            f"{ASSET_NAME} is missing from the {ASSET_PACKAGE} package. It is "
            "built from ui/src — run `npm ci && npm run build` in ui/."
        )
    return asset.read_text(encoding="utf-8")


@mcp.resource(
    TRAIL_MAP_RESOURCE_URI,
    name="trail-map",
    title="Trail map",
    description=(
        "An interactive map of a route returned by `get_route_details` or "
        "`route_between_points`. Renders the geometry already in the tool's "
        "result; it fetches nothing further and adds no facts of its own."
    ),
    mime_type=TRAIL_MAP_MIME_TYPE,
    meta=TRAIL_MAP_RESOURCE_META,
)
def trail_map() -> str:
    """Serve the packaged trail-map document."""
    return trail_map_html()


__all__ = [
    "TRAIL_MAP_MIME_TYPE",
    "TRAIL_MAP_RESOURCE_META",
    "TRAIL_MAP_RESOURCE_URI",
    "TRAIL_MAP_TOOL_META",
    "trail_map_html",
]
