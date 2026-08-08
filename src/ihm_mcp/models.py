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

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ihm_mcp import ATTRIBUTION

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


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
