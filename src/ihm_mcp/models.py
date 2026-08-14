"""The vocabulary every tool answers in.

These models are the contract an LLM sees: field names and descriptions travel
to the client in each tool's output schema, so they are written to be read by a
model, not only by a Python caller. Names are camelCase to match the upstream
API and the URLs they appear in.

Two rules the rest of the server leans on:

* A feature is identified by a `FeatureRef`, never a bare id. `123456` is
  meaningless without knowing it is an OSM relation.
* Every result carries its `Attribution`. The upstream licences require it, and
  a model that is handed data with no provenance will invent some.
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from ihm_mcp import ATTRIBUTION

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]

#: The two languages the map site publishes names and descriptions in.
Language = Literal["he", "en"]

#: Upstream's own difficulty scale, from the map site's route filter. Only some
#: routes carry one — see `RouteSummary.difficulty`.
Difficulty = Literal["Easy", "Moderate", "Hard", "Very Hard"]


class Model(BaseModel):
    """Base for tool inputs and outputs: immutable once built."""

    model_config = ConfigDict(frozen=True)


class Coordinates(Model):
    """A single WGS84 point."""

    lat: Latitude = Field(description="Latitude in decimal degrees (WGS84).")
    lng: Longitude = Field(description="Longitude in decimal degrees (WGS84).")


class BoundingBox(Model):
    """An axis-aligned lat/lng box. Not valid across the antimeridian, which
    the area this server covers never approaches."""

    minLat: Latitude = Field(description="Southern edge, decimal degrees.")
    minLng: Longitude = Field(description="Western edge, decimal degrees.")
    maxLat: Latitude = Field(description="Northern edge, decimal degrees.")
    maxLng: Longitude = Field(description="Eastern edge, decimal degrees.")

    def contains(self, point: Coordinates) -> bool:
        return (
            self.minLat <= point.lat <= self.maxLat
            and self.minLng <= point.lng <= self.maxLng
        )


#: Roughly the extent the Israel Hiking Map covers. It is a box, so it also
#: takes in slivers of neighbouring countries and leaves out nothing that
#: matters here; it exists to rank a worldwide search, not to draw a border.
ISRAEL_BBOX = BoundingBox(minLat=29.3, minLng=34.2, maxLat=33.4, maxLng=35.9)


class FeatureRef(Model):
    """How this server names a map feature — the handle other tools accept.

    Later PRs specialise this for routes and POIs; the pair itself never
    changes shape.
    """

    source: str = Field(
        description="Dataset the feature comes from, e.g. 'OSM'. Sources other "
        "than OSM exist upstream and are not all resolvable by this server."
    )
    identifier: str = Field(
        description="Identifier within that source, e.g. 'relation_282071'. "
        "Meaningful only together with `source`."
    )


def poi_url(base_url: str, ref: FeatureRef, language: Language) -> str:
    """The map site's page for a feature — the link a person can actually open.

    One definition, because every tool that returns a feature returns this link
    and a second spelling of the format would drift from the first.
    """
    source = quote(ref.source, safe="")
    identifier = quote(ref.identifier, safe="")
    return f"{base_url.rstrip('/')}/poi/{source}/{identifier}?language={language}"


class Attribution(Model):
    """Credit that must travel with any use of the data in a result."""

    notice: str = Field(description="Credit line to reproduce alongside this data.")
    sources: list[str] = Field(description="Each contributing dataset and its licence.")


IHM_ATTRIBUTION = Attribution(
    notice=ATTRIBUTION,
    sources=[
        "Israel Hiking Map / Mapeak (https://mapeak.com) — CC BY-NC-SA 3.0",
        "OpenStreetMap contributors (https://www.openstreetmap.org/copyright) — ODbL",
    ],
)


class PlaceResult(Model):
    """One candidate location for a searched name."""

    ref: FeatureRef = Field(description="Identity of the matched feature.")
    title: str = Field(description="The feature's own name in the requested language.")
    displayName: str = Field(
        description="Name with its administrative context, e.g. "
        "'Haifa, Haifa Subdistrict, Israel'. Use this to disambiguate."
    )
    coordinates: Coordinates = Field(
        description="A representative point for the feature — not a trailhead, "
        "entrance, or parking area."
    )
    inIsrael: bool = Field(
        description="Whether the point falls inside the approximate box this "
        "server uses for Israel. Box-shaped, so treat edges as uncertain."
    )
    hasExtraData: bool = Field(
        description="Upstream flag: the map holds extended data (description, "
        "images) for this feature. Not a promise that any tool can resolve it."
    )
    ihmUrl: str = Field(description="Human-viewable page for this feature on the map site.")


class SearchedArea(Model):
    """The circle a search actually covered, echoed back so a caller can see
    what a result is a result *of*."""

    center: Coordinates = Field(description="Point the search was centred on.")
    radiusKm: float = Field(description="Radius searched around that point, in kilometres.")


class RouteSummary(Model):
    """One mapped hiking route, as an area search can describe it.

    Everything here comes from the map's own marker for the route. It says what
    somebody recorded, not what is true on the ground today: nothing in it
    establishes that the route is open, marked, passable, permitted, or safe.
    """

    ref: FeatureRef = Field(
        description="Identity of the route. Pass this to a tool that resolves "
        "route details; not every source can be resolved."
    )
    title: str = Field(description="The route's name in the requested language.")
    description: str | None = Field(
        description="The mapper's own note about the route, when there is one. "
        "Often the only place a closure or a hazard is recorded — and undated, "
        "so it may describe conditions from years ago."
    )
    difficulty: Difficulty | None = Field(
        description="Difficulty as rated upstream. Null for most routes: the "
        "rating is optional in the map data, and its absence says nothing about "
        "how hard the route is."
    )
    lengthKm: float | None = Field(
        description="Length of the mapped route in kilometres, rounded to 10 m. "
        "Null when the map data carries no length."
    )
    startPoint: Coordinates = Field(
        description="Where the map draws the route's start marker. Not a "
        "verified trailhead, parking place, or public access point."
    )
    distanceFromSearchCenterKm: float = Field(
        description="Great-circle distance from the search centre to "
        "`startPoint`, rounded to 10 m. Not a walking distance."
    )
    ihmUrl: str = Field(description="Human-viewable page for this route on the map site.")
