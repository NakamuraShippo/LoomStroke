"""Result of a separation / ink coverage pass for one page (preview only, never persisted)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["SeparationSnapshot"]


@dataclass(frozen=True, slots=True)
class SeparationSnapshot:
    page_id: str
    source_mode: str
    plates: tuple[str, ...]
    plates_on: tuple[str, ...]
    coverage_percent: Mapping[str, float] = field(default_factory=dict)
    max_tac_percent: float = 0.0
    mean_tac_percent: float = 0.0
    over_limit_percent: float = 0.0
    tac_limit_percent: float = 300.0
    width: int = 0
    height: int = 0

    @property
    def exceeds_tac_limit(self) -> bool:
        return self.max_tac_percent > self.tac_limit_percent

    def to_dict(self) -> dict[str, Any]:
        return {
            "pageId": self.page_id,
            "sourceMode": self.source_mode,
            "plates": list(self.plates),
            "platesOn": list(self.plates_on),
            "coveragePercent": {k: round(float(v), 2) for k, v in self.coverage_percent.items()},
            "maxTacPercent": round(float(self.max_tac_percent), 2),
            "meanTacPercent": round(float(self.mean_tac_percent), 2),
            "overLimitPercent": round(float(self.over_limit_percent), 2),
            "tacLimitPercent": float(self.tac_limit_percent),
            "exceedsTacLimit": self.exceeds_tac_limit,
            "width": int(self.width),
            "height": int(self.height),
            "estimate": self.source_mode not in ("cmyk8", "cmyk16"),
        }
