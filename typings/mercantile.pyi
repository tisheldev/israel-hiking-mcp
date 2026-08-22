"""Types for the part of `mercantile` this project uses.

Mercantile ships no type information, and the alternative — silencing it with
`ignore_missing_imports` — makes `Tile` an untyped value, so `tile.z` is
unchecked and `Tile` is not usable as an annotation at all.

Only the six names this project calls are declared. This is not a complete
stub, and it is not meant to become one: anything added here is a claim about
somebody else's library that nothing in this repo verifies.
"""

from collections.abc import Iterator, Sequence
from typing import NamedTuple

class Tile(NamedTuple):
    x: int
    y: int
    z: int

class LngLat(NamedTuple):
    lng: float
    lat: float

class LngLatBbox(NamedTuple):
    west: float
    south: float
    east: float
    north: float

class Bbox(NamedTuple):
    left: float
    bottom: float
    right: float
    top: float

def bounds(*tile: Tile | int) -> LngLatBbox: ...
def xy_bounds(*tile: Tile | int) -> Bbox: ...
def ul(*tile: Tile | int) -> LngLat: ...
def tile(lng: float, lat: float, zoom: int, truncate: bool = ...) -> Tile: ...
def xy(lng: float, lat: float, truncate: bool = ...) -> tuple[float, float]: ...
def lnglat(x: float, y: float, truncate: bool = ...) -> LngLat: ...
def tiles(
    west: float,
    south: float,
    east: float,
    north: float,
    zooms: int | Sequence[int],
    truncate: bool = ...,
) -> Iterator[Tile]: ...
