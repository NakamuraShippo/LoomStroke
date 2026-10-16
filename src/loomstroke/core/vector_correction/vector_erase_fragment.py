"""A retained path and its original normalised arc-length interval."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VectorEraseFragment:
    path: Any
    start: float
    end: float
