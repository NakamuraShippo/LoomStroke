"""Named spot ink: stable ``ink_id`` separate from the user-visible name (CT-24)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["SpotInkDefinition"]


@dataclass(frozen=True, slots=True)
class SpotInkDefinition:
    ink_id: str
    name: str
    alternate_lab: tuple[float, float, float] = (50.0, 0.0, 0.0)
    solidity: float = 1.0
    print_sequence: int = 0

    def validate(self) -> tuple[str, ...]:
        issues: list[str] = []
        if not self.ink_id.strip():
            issues.append("ink_id_required")
        if not self.name.strip():
            issues.append("ink_name_required")
        lightness, a_axis, b_axis = self.alternate_lab
        if not all(math.isfinite(v) for v in (lightness, a_axis, b_axis)):
            issues.append("ink_lab_not_finite")
        elif not 0.0 <= lightness <= 100.0 or abs(a_axis) > 128.0 or abs(b_axis) > 128.0:
            issues.append("ink_lab_out_of_range")
        if not 0.0 <= self.solidity <= 1.0:
            issues.append("ink_solidity_out_of_range")
        return tuple(issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inkId": self.ink_id,
            "name": self.name,
            "alternateLab": list(self.alternate_lab),
            "solidity": float(self.solidity),
            "printSequence": int(self.print_sequence),
        }

    @classmethod
    def from_dict(cls, data: Any) -> SpotInkDefinition | None:
        if not isinstance(data, Mapping):
            return None
        ink_id = data.get("inkId")
        name = data.get("name")
        if not isinstance(ink_id, str) or not isinstance(name, str):
            return None
        lab = data.get("alternateLab", (50.0, 0.0, 0.0))
        try:
            lab_tuple = (float(lab[0]), float(lab[1]), float(lab[2]))
            solidity = float(data.get("solidity", 1.0))
            sequence = int(data.get("printSequence", 0))
        except (TypeError, ValueError, IndexError):
            return None
        return cls(ink_id, name, lab_tuple, solidity, sequence)
