"""Turning a route's identity into its shape, source by source.

`search_hiking_routes` answers with refs like `{"source": "Users",
"identifier": "iXKu2M4BV5"}`, and each source keeps its routes somewhere else,
in a shape of its own: a user's share lives behind the map site's own
`/api/urls/{id}`, an OSM route lives in OpenStreetMap's API. So resolving a
route is an adapter per source behind one protocol, and a registry that says
which sources this server can resolve at all.

A source with no adapter is refused by name. Guessing a URL for it — inventing
`/api/nakeb/{id}` because the identifier looks numeric — is how a server starts
reporting confident nonsense, and a caller can still open the `ihmUrl` that
came back with the search result.

The two adapters here answer the same questions from data that agrees about
almost nothing: a share carries a title, a difficulty and a measured length
because a person filled them in, while an OSM route carries whatever tags its
mappers thought to add. Where a source says nothing, the answer says nothing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import ClassVar, Protocol
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, ValidationError

from ihm_mcp import tiles
from ihm_mcp.app import AppContext
from ihm_mcp.errors import (
    InvalidInputError,
    RouteNotFoundError,
    UnsupportedSourceError,
    UpstreamNotFound,
    UpstreamSchemaChangedError,
)
from ihm_mcp.ihm_client import UpstreamClient
from ihm_mcp.models import (
    Coordinates,
    Difficulty,
    FeatureRef,
    Language,
    Position,
    ResolvedRoute,
    known_difficulty,
    poi_url,
    share_url,
)
from ihm_mcp.osm import OsmApi, OsmFeature
from ihm_mcp.spatial import (
    METRES_PER_KM,
    geometry_of,
    positions,
    reported_km,
    rounded,
    start_of,
)

logger = logging.getLogger(__name__)


class RouteSourceAdapter(Protocol):
    """What every source's adapter has to offer the tool.

    `note` is what a caller should be told about routes from this source at
    all — who recorded them and what that means — as opposed to what happened
    to this particular route, which the tool works out for itself.
    """

    source: ClassVar[str]
    note: ClassVar[str]

    async def resolve(self, ref: FeatureRef, language: Language) -> ResolvedRoute: ...


#: How an adapter is built: from everything one tool call can reach. The two
#: adapters talk to different hosts, so what they need is "a client", not the
#: same client.
AdapterFactory = Callable[[AppContext], RouteSourceAdapter]


# --- the shape a share arrives in --------------------------------------------


class Upstream(BaseModel):
    """Base for the upstream JSON models: unknown fields are somebody else's.

    Every field below is optional even where the map site always sends it. The
    server's own `ShareUrl` class carries fewer fields than the live API does,
    so which of them arrive is not something to bet a tool call on — and a
    JSON `null` is as likely as an absent key.
    """

    model_config = ConfigDict(extra="ignore")


class UpstreamLatLng(Upstream):
    lat: float
    lng: float


class UpstreamSegment(Upstream):
    latlngs: list[UpstreamLatLng] | None = None


class UpstreamRoute(Upstream):
    name: str | None = None
    segments: list[UpstreamSegment] | None = None


class UpstreamDataContainer(Upstream):
    routes: list[UpstreamRoute] | None = None


class UpstreamShare(Upstream):
    """The `/api/urls/{id}` document, as observed live on 2026-08-14.

    `type`, `difficulty`, `length`, `gain`, `loss` and `start` are absent from
    the API's own server-side model but present in what it actually sends;
    that mismatch is the reason nothing here is required except the id.
    """

    id: str
    dataContainer: UpstreamDataContainer | None = None
    title: str | None = None
    description: str | None = None
    type: str | None = None
    difficulty: str | None = None
    length: float | None = None
    gain: float | None = None
    loss: float | None = None
    start: UpstreamLatLng | None = None


# --- the sources this server can resolve -------------------------------------


class UsersAdapter:
    """Routes somebody drew or recorded on the map site and shared by link.

    The share holds the route the way its editor does — a list of routes, each
    a list of segments, each a list of points — so the shape has to be
    reassembled here. Ported from the site's own `convertShareUrlToPoi`.
    """

    source: ClassVar[str] = "Users"
    note: ClassVar[str] = (
        "This route is one person's saved and shared route on the map site, not "
        "an official or waymarked trail. Nobody has checked or approved it, and "
        "its title, description and difficulty are whatever its author wrote."
    )

    def __init__(self, app: AppContext) -> None:
        self.client: UpstreamClient = app.ihm
        self.base_url = str(app.settings.base_url)

    async def resolve(self, ref: FeatureRef, language: Language) -> ResolvedRoute:
        """The shared route behind `ref`, or a typed error saying why not.

        `language` is unused: a share carries the one title and description its
        author typed, in whichever language they chose. It stays in the
        signature because the sources that do hold both languages need it.
        """
        share = await self.fetch(ref.identifier)
        geometry = geometry_of(list(lines(share)))
        if geometry is None:
            # A share is a saved map view, and it can hold only markers, or a
            # route somebody started and left at one point.
            raise RouteNotFoundError(
                f"The share `{ref.identifier}` exists but holds no route line — "
                "it may be a set of markers rather than a route."
            )

        return ResolvedRoute(
            ref=ref,
            title=(share.title or "").strip() or f"Shared route {share.id}",
            description=(share.description or "").strip() or None,
            activity=activity(share.type),
            difficulty=known_difficulty((share.difficulty or "").strip()),
            lengthKm=length_km(share.length),
            ascentMeters=climb(share.gain),
            descentMeters=climb(share.loss),
            startPoint=stated_start(share) or start_of(geometry),
            geometry=geometry,
            ihmUrl=share_url(self.base_url, share.id),
        )

    async def fetch(self, identifier: str) -> UpstreamShare:
        share_id = identifier.strip()
        if not share_id:
            raise InvalidInputError(
                "A `Users` route is identified by its share id, e.g. "
                "'iXKu2M4BV5'; this ref carries an empty identifier."
            )

        try:
            payload = await self.client.get_json(f"/api/urls/{quote(share_id, safe='')}")
        except UpstreamNotFound:
            raise RouteNotFoundError(
                f"No shared route with id `{share_id}` exists on "
                f"{self.client.host}. Shares can be deleted by their author."
            ) from None

        try:
            return UpstreamShare.model_validate(payload)
        except ValidationError as exc:
            logger.warning(
                "unrecognised share %s from %s: %s", share_id, self.client.host, exc
            )
            raise UpstreamSchemaChangedError(
                f"{self.client.host} described the shared route `{share_id}` in a "
                "shape this server does not recognise, so it was discarded "
                "rather than guessed at."
            ) from None


class OsmAdapter:
    """Routes mapped in OpenStreetMap, which is most of what the map draws.

    The geometry comes from `api.openstreetmap.org` rather than from the map
    site, because that is where it lives and what the site's own frontend does.
    Everything reported about the route is an OSM tag, so most of it is absent
    on most routes — a relation is often no more than `route=hiking` and a name.
    """

    source: ClassVar[str] = "OSM"
    note: ClassVar[str] = (
        "This route is mapped in OpenStreetMap by volunteers. Anyone can edit "
        "it, nobody checks it, and it records where a trail was mapped rather "
        "than an official or maintained route — the mapping may be years old."
    )

    def __init__(self, app: AppContext) -> None:
        self.api = OsmApi(
            app.osm, max_requests=app.settings.max_osm_requests_per_tool_call
        )
        self.base_url = str(app.settings.base_url)

    async def resolve(self, ref: FeatureRef, language: Language) -> ResolvedRoute:
        """The OSM feature behind `ref`, or a typed error saying why not."""
        feature = OsmFeature.parse(ref.identifier)
        route = await self.api.route(feature)
        geometry = geometry_of(route.lines)
        if geometry is None:
            raise RouteNotFoundError(
                f"OpenStreetMap's {feature} draws no line, so it is not a route "
                "this server can return — it may be an area, a boundary or a "
                "collection of points."
            )

        tags = route.tags
        return ResolvedRoute(
            ref=ref,
            title=tiles.title(tags, language) or f"OpenStreetMap {feature}",
            description=tiles.description(tags, language) or None,
            activity=osm_activity(tags),
            difficulty=osm_difficulty(tags),
            lengthKm=osm_length_km(tags),
            ascentMeters=climb(tiles.number(tags, "ascent")),
            descentMeters=climb(tiles.number(tags, "descent")),
            startPoint=start_of(geometry),
            geometry=geometry,
            ihmUrl=poi_url(self.base_url, ref, language),
        )


#: Every source this server can turn into geometry. A ref naming anything else
#: — `Nakeb`, `iNature`, `Wikidata` — is refused, not attempted.
ADAPTERS: dict[str, AdapterFactory] = {
    UsersAdapter.source: UsersAdapter,
    OsmAdapter.source: OsmAdapter,
}


def adapter_for(source: str, app: AppContext) -> RouteSourceAdapter:
    """The adapter for a source, or an error naming the ones that exist."""
    factory = ADAPTERS.get(source)
    if factory is None:
        resolvable = ", ".join(sorted(ADAPTERS)) or "none"
        raise UnsupportedSourceError(
            f"This server cannot resolve routes from `{source}`; it resolves "
            f"{resolvable}. Open the route's `ihmUrl` on the map site instead."
        )
    return factory(app)


# --- reading one share -------------------------------------------------------


def lines(share: UpstreamShare) -> Iterator[list[Position]]:
    """Each route in the share as one line of positions.

    A share's routes are separate lines: two days of the same trip, or a walk
    in and a walk out. Their segments, though, are one line — the editor cuts a
    route at every point the user dropped — so those are joined end to end, as
    the map site itself does when it draws a share.
    """
    container = share.dataContainer or UpstreamDataContainer()
    for route in container.routes or []:
        points = [
            Coordinates(lat=point.lat, lng=point.lng)
            for segment in route.segments or []
            for point in segment.latlngs or []
        ]
        line = positions(points)
        if len(line) < 2:
            logger.debug("route %r in share %s draws no line", route.name, share.id)
        yield line


def stated_start(share: UpstreamShare) -> Coordinates | None:
    """`start`: where the share itself says the route begins.

    Preferred over the first position of the line, which is the same point when
    both exist — but the share is the one upstream computed, and a route whose
    first line is not the one its author started on keeps their answer.
    """
    if share.start is None:
        return None
    return rounded(Coordinates(lat=share.start.lat, lng=share.start.lng))


def activity(kind: str | None) -> str | None:
    """What the route was recorded for. Upstream writes the literal string
    "Unknown" when nobody said, which is not an activity."""
    named = (kind or "").strip()
    return None if named in ("", "Unknown") else named


def length_km(metres: float | None) -> float | None:
    """The share's own length in kilometres. Upstream writes zero for a route
    it never measured, so zero is an absent length rather than a short route."""
    if metres is None or metres <= 0:
        return None
    return reported_km(metres / METRES_PER_KM)


def climb(metres: float | None) -> int | None:
    """Ascent or descent as a positive whole number of metres.

    Upstream sends loss as a negative number; a caller reading `descentMeters`
    wants how far down, not a signed delta. Whole metres because the figure
    comes from an elevation model, which is nowhere near decimetre-accurate.
    """
    if metres is None or metres == 0:
        return None
    return round(abs(metres))


# --- reading one OSM feature's tags ------------------------------------------

#: `route=*` as the map site's own `setIconColorCategory` reads it, so a route
#: resolved here is described in the same words the search that found it used.
OSM_ACTIVITIES = {
    "hiking": "Hiking",
    "foot": "Hiking",
    "bicycle": "Bicycle",
    "mtb": "Bicycle",
}


def osm_activity(tags: tiles.Properties) -> str | None:
    """What the route is for, from its `route` tag.

    A value the map site has no word for is passed through as OSM wrote it —
    `ski`, `horse` — because "some other kind of route" is still more than the
    null that a missing tag earns.
    """
    route = tiles.text(tags, "route").lower()
    if not route:
        return None
    if route == "road" and tiles.text(tags, "scenic").lower() == "yes":
        return "4x4"
    return OSM_ACTIVITIES.get(route, route)


def osm_difficulty(tags: tiles.Properties) -> Difficulty | None:
    """A `difficulty` tag, where it happens to say one of upstream's grades.

    OSM has no agreed scale for this — most routes carry no such tag at all,
    and the ones that do were graded by whoever typed it. Anything outside the
    four grades this server knows how to mean is reported as no grade.
    """
    return known_difficulty(tiles.text(tags, "difficulty").title())


def osm_length_km(tags: tiles.Properties) -> float | None:
    """`distance`: how long the route's mappers say it is, in kilometres.

    Kilometres is the OSM convention for a bare number, and a value carrying
    any other unit is left as no length rather than converted on a guess. This
    is the tag's claim, not a measurement of the geometry — the two can differ,
    and a length nobody wrote down stays null.

    `length` is deliberately not read, though local route relations carry it
    more often than `distance`: it is nobody's convention, it is metres on
    other kinds of feature, and one sampled Haifa Trail section tags `length=3`
    where the map's own computed length is 8.59 km. The search result's
    `lengthKm` is that computed figure, and it is the one to trust.
    """
    stated = tiles.text(tags, "distance").removesuffix("km").strip()
    try:
        kilometres = float(stated)
    except ValueError:
        return None
    return reported_km(kilometres) if kilometres > 0 else None
