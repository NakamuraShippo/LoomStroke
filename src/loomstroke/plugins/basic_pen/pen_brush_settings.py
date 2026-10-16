"""Private brush snapshot for pen tips, including their hardness interpretation."""

from __future__ import annotations

from dataclasses import dataclass, fields

from ...core.commands import BrushSettings


@dataclass(frozen=True)
class _PenBrushSettings(BrushSettings):
    hardness_mode: str = "radial"

    @classmethod
    def from_brush(cls, brush: BrushSettings, hardness_mode: str) -> _PenBrushSettings:
        values = {setting.name: getattr(brush, setting.name) for setting in fields(BrushSettings)}
        return cls(**values, hardness_mode=hardness_mode)
