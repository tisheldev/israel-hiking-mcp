"""`search_places` — turning a name somebody typed into candidate coordinates.

Two things about the upstream endpoint shape this module.

It is a **worldwide** index. `GET /api/search/Haifa` answers with Haifa in
Syria, Nablus, the United States, France and Chile before it gets to Haifa,
Israel. Nothing in the response says which country a hit is in beyond the
`displayName` text, so this server decides geographically, with a bounding box.

It has **no contract**. There is no schema for the response, so it is parsed
into a model here and any surprise is reported as `upstream_schema_changed`
rather than guessed at.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from urllib.parse import quote

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ihm_mcp.app import ToolContext, app_context, tool
from ihm_mcp.errors import (
    InvalidInputError,
    UpstreamNotFound,
    UpstreamSchemaChangedError,
    tool_errors,
)
from ihm_mcp.models import (
    IHM_ATTRIBUTION,
    ISRAEL_BBOX,
    Attribution,
    Coordinates,
    FeatureRef,
    Language,
    Model,
    PlaceResult,
    poi_url,
)

logger = logging.getLogger(__name__)

MIN_QUERY_CHARS = 2
MAX_QUERY_CHARS = 100
DEFAULT_LIMIT = 10
MAX_LIMIT = 20


class UpstreamLocation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lat: float
    lng: float


class UpstreamPlace(BaseModel):
    """The upstream `/api/search` item, as observed on 2026-08-07.

    `id`, `source` and `location` are required because a result without them is
    not usable. The rest are tolerated as missing: a nameless feature is still
    a real coordinate, and losing the whole search over one is worse.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    source: str
    location: UpstreamLocation
    title: str = ""
    displayName: str = ""
    hasExtraData: bool = False


UPSTREAM_PLACES = TypeAdapter(list[UpstreamPlace])


class PlaceSearchResult(Model):
    """Candidate places for one query, plus how the search was interpreted."""

    query: str = Field(description="The trimmed term actually sent upstream.")
    language: Language = Field(description="Language the names were requested in.")
    israelOnly: bool = Field(description="Whether non-Israel matches were dropped.")
    places: list[PlaceResult] = Field(
        description="Candidates, best first: inside Israel before outside, and "
        "within each group in upstream relevance order. Empty when nothing matched."
    )
    warnings: list[str] = Field(
        description="Things that affected this result — filtering, truncation, "
        "no matches. Worth relaying to the user."
    )
    attribution: Attribution = Field(description="Required credit for this data.")


def normalize_query(query: str) -> str:
    """Trim and bound the term, or explain what a usable one looks like.

    The schema already bounds the raw string; this runs after trimming, where
    the length can differ and where '   ' — which upstream answers with a 500 —
    becomes visible.
    """
    term = " ".join(query.split())
    if len(term) < MIN_QUERY_CHARS:
        raise InvalidInputError(
            f"`query` must contain at least {MIN_QUERY_CHARS} non-blank characters; "
            f"got {len(term)}. Pass a place name such as 'Haifa' or 'עין חמד'."
        )
    return term


def normalize(
    payload: Any, *, host: str, base_url: str, language: Language
) -> list[PlaceResult]:
    """Upstream JSON to `PlaceResult`s, ranked Israel-first.

    Sorting is stable, so upstream's relevance order survives inside each
    group: same query, same order, every time.
    """
    try:
        raw_places = UPSTREAM_PLACES.validate_python(payload)
        places = [
            place_result(raw, base_url=base_url, language=language) for raw in raw_places
        ]
    except ValidationError as exc:
        logger.warning("unrecognised search response from %s: %s", host, exc)
        raise UpstreamSchemaChangedError(
            f"{host} answered the place search in a shape this server does not "
            "recognise, so the results were discarded rather than guessed at."
        ) from None
    return sorted(places, key=lambda place: not place.inIsrael)


def place_result(raw: UpstreamPlace, *, base_url: str, language: Language) -> PlaceResult:
    ref = FeatureRef(source=raw.source, identifier=raw.id)
    coordinates = Coordinates(lat=raw.location.lat, lng=raw.location.lng)
    # Upstream sends "" for both names on some features; fall back rather than
    # hand a model an unlabelled point.
    title = raw.title.strip() or raw.displayName.strip() or raw.id
    return PlaceResult(
        ref=ref,
        title=title,
        displayName=raw.displayName.strip() or title,
        coordinates=coordinates,
        inIsrael=ISRAEL_BBOX.contains(coordinates),
        hasExtraData=raw.hasExtraData,
        ihmUrl=poi_url(base_url, ref, language),
    )


def warnings_for(
    *, matched: int, elsewhere: int, shown: int, kept: int, israel_only: bool
) -> list[str]:
    """Everything that happened between the upstream answer and this result."""
    warnings: list[str] = []
    if matched == 0:
        warnings.append(
            "Nothing matched this name. Try the other language, a different "
            "spelling, or a nearby larger place."
        )
    elif kept == 0:
        warnings.append(
            f"No matches inside Israel, but {elsewhere} matched elsewhere in the "
            "world. Set `israelOnly` to false to see them."
        )
    elif israel_only and elsewhere:
        warnings.append(
            f"{elsewhere} match(es) outside Israel were omitted. Set `israelOnly` "
            "to false to include them."
        )
    if shown < kept:
        warnings.append(
            f"Showing {shown} of {kept} matches. Narrow the query or raise `limit`."
        )
    return warnings


@tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
@tool_errors
async def search_places(
    ctx: ToolContext,
    query: Annotated[
        str,
        Field(
            description="Place name to look up, in Hebrew or English, e.g. "
            "'Haifa', 'עין חמד', 'Mount Meron'. Not an address or a description.",
            min_length=1,
            max_length=MAX_QUERY_CHARS,
        ),
    ],
    israelOnly: Annotated[
        bool,
        Field(
            description="Keep only matches inside Israel. Set false to include "
            "worldwide matches, which are then listed after the Israeli ones."
        ),
    ] = True,
    language: Annotated[
        Language,
        Field(description="Language to return names in: 'he' Hebrew, 'en' English."),
    ] = "en",
    limit: Annotated[
        int,
        Field(description="Maximum number of candidates to return.", ge=1, le=MAX_LIMIT),
    ] = DEFAULT_LIMIT,
) -> PlaceSearchResult:
    """Find places on the Israel Hiking Map by name and get their coordinates.

    Use this to turn a name into coordinates for the other tools here. It
    returns candidates ranked best-first and never picks one — when more than
    one is plausible, show the list and ask.

    The underlying search is worldwide, so a name can match places far from
    Israel. By default only Israeli matches are returned; the `warnings` field
    says when others were dropped.

    What a match does not tell you: whether the place is open, reachable,
    permitted, safe, or has water or parking. The coordinates mark the mapped
    feature itself, not a trailhead or an entrance. A result means the name
    exists in map data — nothing about conditions on the ground today.
    """
    app = app_context(ctx)
    term = normalize_query(query)

    try:
        payload = await app.ihm.get_json(
            f"/api/search/{quote(term, safe='')}", params={"language": language}
        )
    except UpstreamNotFound:
        # A term with no match answers 200 with `[]`, so a 404 here means the
        # endpoint moved rather than that the place is unknown.
        raise UpstreamSchemaChangedError(
            f"The place search endpoint is gone from {app.ihm.host}."
        ) from None

    places = normalize(
        payload,
        host=app.ihm.host,
        base_url=str(app.settings.base_url),
        language=language,
    )
    elsewhere = sum(not place.inIsrael for place in places)
    kept = [place for place in places if place.inIsrael] if israelOnly else places

    return PlaceSearchResult(
        query=term,
        language=language,
        israelOnly=israelOnly,
        places=kept[:limit],
        warnings=warnings_for(
            matched=len(places),
            elsewhere=elsewhere,
            shown=len(kept[:limit]),
            kept=len(kept),
            israel_only=israelOnly,
        ),
        attribution=IHM_ATTRIBUTION,
    )
