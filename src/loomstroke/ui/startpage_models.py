"""StartPage models and preset definitions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..core.new_document_color_spec import NewDocumentColorSpec

MULTIPLE_OF_64 = 64


@dataclass(frozen=True)
class BackgroundSpec:
    """Background selection for a new document."""

    kind: str  # "transparent" | "solid"
    color_rgba: tuple[int, int, int, int] | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "color_rgba": list(self.color_rgba) if self.color_rgba is not None else None,
        }

    @staticmethod
    def from_payload(payload: Mapping[str, Any] | None) -> BackgroundSpec:
        if not isinstance(payload, Mapping):
            return BackgroundSpec(kind="transparent", color_rgba=None)
        kind = str(payload.get("kind", "transparent"))
        raw_color = payload.get("color_rgba")
        if isinstance(raw_color, Iterable):
            values = [int(v) for v in list(raw_color)[:4]]
            while len(values) < 4:
                values.append(255)
            return BackgroundSpec(kind=kind, color_rgba=tuple(values))  # type: ignore[arg-type]  # variable-length tuple vs fixed 4-tuple
        return BackgroundSpec(kind=kind, color_rgba=None)


@dataclass(frozen=True)
class StartPageCanvasSpec:
    """Canvas specification used by StartPage."""

    width_px: int
    height_px: int
    dpi: int
    background: BackgroundSpec
    auto_fix_multiple_of_64: bool = True
    color: NewDocumentColorSpec = field(default_factory=NewDocumentColorSpec)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "width_px": int(self.width_px),
            "height_px": int(self.height_px),
            "dpi": int(self.dpi),
            "background": self.background.to_payload(),
            "auto_fix_multiple_of_64": bool(self.auto_fix_multiple_of_64),
        }
        if self.color.mode_id != "rgba8":
            payload["color"] = self.color.to_payload()
        return payload

    @staticmethod
    def from_payload(payload: Mapping[str, Any]) -> StartPageCanvasSpec:
        width = int(payload.get("width_px", 1))
        height = int(payload.get("height_px", 1))
        dpi = int(payload.get("dpi", 72))
        background = BackgroundSpec.from_payload(payload.get("background"))
        auto_fix = bool(payload.get("auto_fix_multiple_of_64", False))
        return StartPageCanvasSpec(
            width_px=width,
            height_px=height,
            dpi=dpi,
            background=background,
            auto_fix_multiple_of_64=auto_fix,
            color=NewDocumentColorSpec.from_payload(payload.get("color")),
        )


@dataclass(frozen=True)
class CanvasPreset:
    """Canvas preset entry."""

    id: str
    label: str
    width_px: int
    height_px: int
    notes: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "width_px": int(self.width_px),
            "height_px": int(self.height_px),
            "notes": self.notes,
        }

    @staticmethod
    def from_payload(payload: Mapping[str, Any]) -> CanvasPreset:
        return CanvasPreset(
            id=str(payload.get("id", "")) or "preset",
            label=str(payload.get("label", "")) or "Preset",
            width_px=int(payload.get("width_px", 1)),
            height_px=int(payload.get("height_px", 1)),
            notes=payload.get("notes") if isinstance(payload.get("notes"), str) else None,
        )


@dataclass(frozen=True)
class PresetCategory:
    """Preset category."""

    id: str
    label: str
    presets: tuple[CanvasPreset, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "presets": [preset.to_payload() for preset in self.presets],
        }

    @staticmethod
    def from_payload(payload: Mapping[str, Any]) -> PresetCategory:
        raw_presets = payload.get("presets")
        presets: list[CanvasPreset] = []
        if isinstance(raw_presets, list):
            for entry in raw_presets:
                if isinstance(entry, Mapping):
                    presets.append(CanvasPreset.from_payload(entry))
        return PresetCategory(
            id=str(payload.get("id", "")) or "category",
            label=str(payload.get("label", "")) or "Category",
            presets=tuple(presets),
        )


def round_to_multiple(value: int, multiple: int = MULTIPLE_OF_64) -> int:
    """Round to the nearest multiple (ties round down)."""

    if multiple <= 0:
        return max(1, int(value))
    v = int(value)
    down = (v // multiple) * multiple
    up = ((v + multiple - 1) // multiple) * multiple
    if abs(v - down) < abs(v - up):
        return max(multiple, down)
    if abs(v - down) > abs(v - up):
        return max(multiple, up)
    return max(multiple, down)


def auto_fix_dimensions(
    width_px: int,
    height_px: int,
    *,
    multiple: int = MULTIPLE_OF_64,
) -> tuple[int, int]:
    """Return width/height rounded to the nearest multiple of ``multiple``."""

    return round_to_multiple(width_px, multiple), round_to_multiple(height_px, multiple)


__all__ = [
    "MULTIPLE_OF_64",
    "BackgroundSpec",
    "CanvasPreset",
    "PresetCategory",
    "StartPageCanvasSpec",
    "auto_fix_dimensions",
    "round_to_multiple",
]
