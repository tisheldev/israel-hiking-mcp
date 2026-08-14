"""Reading values out of a tile feature's property bag.

`decode` hands the bag over exactly as upstream wrote it, so every tool that
reads one meets the same three problems: names are per-language and the keys
overlap (`name`, `name:he`, `name:en`), a value may be any scalar the MVT
format allows, and nothing is guaranteed to be there at all.

The language fallbacks are transcribed from the map site's `GeoJSONUtils`,
which is where the order is decided for what a visitor sees.
"""

from __future__ import annotations

from typing import Any

from ihm_mcp.models import Language

Properties = dict[str, Any]

#: `getTitle` in `geojson-utils.ts` looks for a mountain-bike name once the
#: ordinary ones are exhausted; some cycling routes carry only that.
NAME_KEYS = ("name", "mtb:name")


def text(properties: Properties, key: str) -> str:
    """One property as a trimmed string, or '' if it is absent or not text."""
    value = properties.get(key)
    return value.strip() if isinstance(value, str) else ""


def number(properties: Properties, key: str) -> float | None:
    """One property as a number, or None if it is absent or not one.

    Numeric strings are accepted: MVT can carry a number as either, and which
    one arrives is upstream's choice, not a fact about the feature.
    """
    value = properties.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value) if isinstance(value, str) else None
    except ValueError:
        return None


def title(properties: Properties, language: Language) -> str:
    """The feature's name, in the requested language where there is one.

    Ported from `getTitle`: the asked-for language, then English, then the
    unqualified name — and only then the same three for `mtb:name`. Empty when
    the feature is unnamed, which the map site treats as "do not show this".
    """
    for key in NAME_KEYS:
        for candidate in (f"{key}:{language}", f"{key}:en", key):
            if found := text(properties, candidate):
                return found
    return ""


def description(properties: Properties, language: Language) -> str:
    """The mapper's note about the feature, in the requested language.

    Ported from `getDescription`, which has no English step: a description is
    free text somebody wrote, and the site would rather show none than one in a
    language the reader did not ask for.
    """
    return text(properties, f"description:{language}") or text(properties, "description")
