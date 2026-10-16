"""Per-object / per-layer overprint intent (CT-24): fill, stroke, process-black-only."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

__all__ = ["OverprintAttributes"]


@dataclass(frozen=True, slots=True)
class OverprintAttributes:
    fill: bool = False
    stroke: bool = False
    process_black_only: bool = True

    METADATA_KEY: ClassVar[str] = "loomstroke.comictools.print.overprint"

    @property
    def is_default(self) -> bool:
        return not self.fill and not self.stroke and self.process_black_only

    def to_dict(self) -> dict[str, Any]:
        return {
            "fill": bool(self.fill),
            "stroke": bool(self.stroke),
            "processBlackOnly": bool(self.process_black_only),
        }

    @classmethod
    def from_dict(cls, data: Any) -> OverprintAttributes:
        if not isinstance(data, Mapping):
            return cls()
        return cls(
            bool(data.get("fill", False)),
            bool(data.get("stroke", False)),
            bool(data.get("processBlackOnly", True)),
        )
