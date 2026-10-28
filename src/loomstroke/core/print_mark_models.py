"""Session-only print mark options and point-coordinate primitives."""

from __future__ import annotations

import math
from dataclasses import dataclass

MARK_MARGIN_PT = 17.0
MARK_LENGTH_PT = 14.0
MARK_GAP_PT = 3.0


@dataclass(frozen=True, slots=True)
class CropMark:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True, slots=True)
class PrintMarkOptions:
    style: str = "jp_corner"
    crop: bool = True
    bleed: bool = False
    registration: bool = False
    center: bool = False
    width_pt: float = 0.25
    offset_pt: float = MARK_GAP_PT
    color: str = "legacy"

    def __post_init__(self) -> None:
        if self.style not in ("jp_corner", "jp_double", "western"):
            raise ValueError("Unknown print mark style")
        if self.color not in ("legacy", "k_only", "registration"):
            raise ValueError("Unknown print mark colour")
        if not math.isfinite(self.width_pt) or not 0.01 <= self.width_pt <= 5:
            raise ValueError("Invalid print mark width")
        if not math.isfinite(self.offset_pt) or not 0 <= self.offset_pt <= 144:
            raise ValueError("Invalid print mark offset")

    @property
    def gap(self) -> float:
        return max(self.offset_pt, self.width_pt / 2 + 0.001)

    @property
    def margin(self) -> float:
        if self == PrintMarkOptions():
            return MARK_MARGIN_PT
        return max(MARK_MARGIN_PT, self.gap + MARK_LENGTH_PT + self.width_pt / 2)
