"""One output stroke of an assisted input stroke: the master or a symmetry copy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["AssistedStroke"]


@dataclass(frozen=True, slots=True)
class AssistedStroke:
    role: Literal["master", "copy"]
    index: int
    points: tuple[tuple[float, float], ...]

    @property
    def is_master(self) -> bool:
        return self.role == "master"
