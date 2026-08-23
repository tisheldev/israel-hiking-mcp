"""OpenStreetMap's own data model, as much of it as a route's shape needs.

Routes on the map site are OSM routes: the site draws them from its tiles, and
when somebody opens one it fetches the geometry from `api.openstreetmap.org`
directly. This server does the same, so this module is the second upstream —
a different host, a different licence, and a usage policy of its own.

Three things about OSM decide everything here:

* A route is a **relation**: an ordered list of members, mostly ways, each way
  an ordered list of nodes. Nothing in it is a line until the nodes are looked
  up, which is what `/full` is for — it answers with the relation and every
  element it references.
* `/full` does **not** expand nested relations. A long trail is usually a
  relation of relations, one per section, and each of those costs another
  request. That recursion is where an innocent-looking call can turn into
  dozens, so it is bounded and the bound is a setting.
* A way is drawn in whatever direction its mapper drew it, and listed in
  whatever order the relation holds. The pieces of one continuous trail have to
  be sewn back together — `spatial.merge_lines` — or the answer claims a trail
  is forty disconnected fragments.

`osm2geojson` would convert an element list wholesale, including the polygon
assembly none of this needs; what is needed instead is the narrow path from
elements to lines, so that is what this module is.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal, Self, cast, get_args

from pydantic import BaseModel, ConfigDict, ValidationError

from ihm_mcp.errors import (
    GeometryTooLargeError,
    InvalidInputError,
    RouteNotFoundError,
    UpstreamNotFound,
    UpstreamSchemaChangedError,
)
from ihm_mcp.ihm_client import UpstreamClient
from ihm_mcp.models import Coordinates, Model, Position
from ihm_mcp.spatial import merge_lines, positions

logger = logging.getLogger(__name__)

#: The three kinds of thing OSM holds. A route is a relation, occasionally a
#: single way; a node is a point and can never be a route.
Kind = Literal["node", "way", "relation"]

KINDS = get_args(Kind)

#: Ids are positive and written in decimal, so `relation_0` and `way_-3` are
#: refs to nothing rather than refs to fetch.
IDENTIFIER = re.compile(rf"^({'|'.join(KINDS)})_([1-9][0-9]*)$")


class OsmFeature(Model):
    """One OSM element's identity, unpacked from a `relation_282071` ref."""

    kind: Kind
    osmId: int

    @classmethod
    def parse(cls, identifier: str) -> Self:
        """The feature a ref names, or an error showing the form expected.

        Nothing is guessed: an identifier this server cannot read is refused
        rather than turned into a URL that would fetch somebody else's data.
        """
        match = IDENTIFIER.match(identifier.strip().lower())
        if match is None:
            raise InvalidInputError(
                f"`{identifier.strip()}` is not an OpenStreetMap identifier. The "
                "form is `<node|way|relation>_<id>`, e.g. 'relation_282071' — "
                "the `identifier` of a search result's `ref`."
            )
        # IDENTIFIER's first group is the alternation of exactly these three.
        return cls(kind=cast(Kind, match.group(1)), osmId=int(match.group(2)))

    def __str__(self) -> str:
        return f"{self.kind} {self.osmId}"

    @property
    def path(self) -> str:
        """Where OSM answers with this element and everything it references.

        `/full` exists for ways and relations; a node is already whole.
        """
        tail = "" if self.kind == "node" else "/full"
        return f"{self.kind}/{self.osmId}{tail}.json"


# --- the shape OSM answers in ------------------------------------------------


class OsmJson(BaseModel):
    """Base for the OSM API's own shapes: unknown fields are OSM's business.

    Only what a route's geometry and headline need is declared, and all of it
    except an element's type and id is optional — one payload carries nodes,
    ways and relations in a single list, so most fields are absent from most
    elements by design.
    """

    model_config = ConfigDict(extra="ignore")


class OsmMember(OsmJson):
    type: str
    ref: int
    role: str | None = None


class OsmElement(OsmJson):
    type: str
    id: int
    lat: float | None = None
    lon: float | None = None
    nodes: list[int] | None = None
    members: list[OsmMember] | None = None
    tags: dict[str, Any] | None = None


class OsmPayload(OsmJson):
    """One `/api/0.6/...json` response. `elements` is required: an answer
    without it is not an answer this server understands."""

    elements: list[OsmElement]


@dataclass
class Elements:
    """Every element fetched so far, indexed by what each is looked up by."""

    nodes: dict[int, Coordinates] = field(default_factory=dict)
    ways: dict[int, OsmElement] = field(default_factory=dict)
    relations: dict[int, OsmElement] = field(default_factory=dict)

    def absorb(self, payload: OsmPayload) -> None:
        """Index one response, leaving what is already known in place.

        Elements repeat across responses — a nested relation arrives with its
        parent and again with its own — and the same element is the same
        element either way. Keeping the first keeps the walk over them stable.
        """
        for element in payload.elements:
            if element.type == "node":
                point = node_point(element)
                if point is not None:
                    self.nodes.setdefault(element.id, point)
            elif element.type == "way":
                self.ways.setdefault(element.id, element)
            elif element.type == "relation":
                self.relations.setdefault(element.id, element)

    def element(self, feature: OsmFeature) -> OsmElement | None:
        index = self.ways if feature.kind == "way" else self.relations
        return index.get(feature.osmId)


def node_point(element: OsmElement) -> Coordinates | None:
    """A node's position, or nothing if it arrived without one."""
    if element.lat is None or element.lon is None:
        return None
    try:
        return Coordinates(lat=element.lat, lng=element.lon)
    except ValidationError:
        logger.warning("node %s is outside the world: %s", element.id, element)
        return None


# --- one route, fetched ------------------------------------------------------


class OsmRoute(Model):
    """What OSM holds about one feature: its tags, and the lines it draws."""

    feature: OsmFeature
    tags: dict[str, Any]
    lines: list[list[Position]]


class OsmApi:
    """Reading routes out of the OpenStreetMap API, within a request budget.

    OSM's usage policy asks for an identifying User-Agent and for restraint.
    The client this is built with carries the first and caches what it fetches;
    `max_requests` is the second, and it is what stops a relation of relations
    from turning one tool call into a crawl.
    """

    def __init__(self, client: UpstreamClient, *, max_requests: int) -> None:
        self.client = client
        self.max_requests = max_requests

    async def route(self, feature: OsmFeature) -> OsmRoute:
        """The lines and tags of one OSM feature.

        A node is refused without asking OSM: a point cannot be a route, and
        the courteous thing to do with a request that has no useful answer is
        not to make it.
        """
        if feature.kind == "node":
            raise InvalidInputError(
                f"`{feature.kind}_{feature.osmId}` is a single mapped point in "
                "OpenStreetMap, not a route. Routes are relations, occasionally "
                "ways."
            )

        elements = await self.elements(feature)
        element = elements.element(feature)
        if element is None:
            raise UpstreamSchemaChangedError(
                f"OpenStreetMap answered for {feature} without describing it, so "
                "the response was discarded rather than guessed at."
            )

        return OsmRoute(
            feature=feature,
            tags=element.tags or {},
            lines=merge_lines(feature_lines(elements, feature)),
        )

    async def elements(self, feature: OsmFeature) -> Elements:
        """Everything the feature's shape needs, nested relations included.

        Breadth-first from the relation asked for, so the requests a route
        costs are spent on its own sections before their sub-sections, and the
        same route costs the same requests every time.
        """
        elements = Elements()
        if feature.kind != "relation":
            elements.absorb(await self.fetch(feature))
            return elements

        pending = deque([feature.osmId])
        fetched: set[int] = set()
        while pending:
            osm_id = pending.popleft()
            if osm_id in fetched:
                continue
            if len(fetched) >= self.max_requests:
                raise GeometryTooLargeError(
                    f"This route is built from more than {self.max_requests} "
                    "nested OpenStreetMap relations, which is more than this "
                    "server will fetch for one call. Ask for one of its sections, "
                    "or open it on the map site."
                )

            elements.absorb(await self.fetch(OsmFeature(kind="relation", osmId=osm_id)))
            fetched.add(osm_id)
            relation = elements.relations.get(osm_id)
            if relation is not None:
                pending.extend(
                    member.ref
                    for member in relation.members or []
                    if member.type == "relation" and member.ref not in fetched
                )

        logger.debug("%s needed %d OSM request(s)", feature, len(fetched))
        return elements

    async def fetch(self, feature: OsmFeature) -> OsmPayload:
        """One element and everything it references, or a typed error."""
        try:
            payload = await self.client.get_json(feature.path)
        except UpstreamNotFound:
            # OSM answers 410 Gone for something deleted, which the client
            # reports the same way: either is a route nobody can walk to.
            raise RouteNotFoundError(
                f"OpenStreetMap holds no {feature}. It may have been deleted, or "
                "the identifier may belong to a different map."
            ) from None

        try:
            return OsmPayload.model_validate(payload)
        except ValidationError as exc:
            logger.warning("unrecognised OSM payload for %s: %s", feature, exc)
            raise UpstreamSchemaChangedError(
                f"OpenStreetMap described {feature} in a shape this server does "
                "not recognise, so it was discarded rather than guessed at."
            ) from None


def feature_lines(elements: Elements, feature: OsmFeature) -> list[list[Position]]:
    """The lines a feature draws, in the order its members are listed.

    A way appears once however many times it is a member: a route that runs out
    and back over the same path, or two sections sharing a link, would otherwise
    draw it twice. Nodes are skipped — a route's node members are the
    guideposts and viewpoints along it, not its path.
    """
    lines: list[list[Position]] = []
    drawn: set[int] = set()

    def draw(way_id: int) -> None:
        if way_id in drawn:
            return
        drawn.add(way_id)
        way = elements.ways.get(way_id)
        line = way_line(elements, way) if way is not None else None
        if line is not None:
            lines.append(line)

    def walk(relation_id: int, visited: set[int]) -> None:
        # A relation can hold itself, directly or round a longer loop; without
        # this the recursion never ends.
        if relation_id in visited:
            logger.debug("relation %s is nested inside itself", relation_id)
            return
        visited.add(relation_id)

        relation = elements.relations.get(relation_id)
        for member in (relation.members if relation is not None else None) or []:
            if member.type == "way":
                draw(member.ref)
            elif member.type == "relation":
                walk(member.ref, visited)

    if feature.kind == "way":
        draw(feature.osmId)
    else:
        walk(feature.osmId, set())
    return lines


def way_line(elements: Elements, way: OsmElement) -> list[Position] | None:
    """One way as a line, or nothing when it does not draw one.

    A way whose nodes did not all arrive is left out entirely rather than drawn
    with the gap closed up: a line through a point the route never passed is a
    worse answer than a missing piece, which at least shows as a gap.
    """
    points: list[Coordinates] = []
    for node_id in way.nodes or []:
        point = elements.nodes.get(node_id)
        if point is None:
            logger.warning(
                "way %s references node %s, which OSM did not send", way.id, node_id
            )
            return None
        points.append(point)

    line = positions(points)
    return line if len(line) >= 2 else None
