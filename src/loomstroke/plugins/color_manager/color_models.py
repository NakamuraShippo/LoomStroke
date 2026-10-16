"""Data models used by the color manager plugin."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from loomstroke.core.color_state import ColorDescriptor

logger = logging.getLogger(__name__)

DEFAULT_PALETTE_ID = "default"


class SwatchId(StrEnum):
    """Swatch identifier helpers for built-in slots."""

    AUTO = "auto"
    CUSTOM = "custom"


@dataclass(frozen=True)
class PaletteSwatch:
    """Represents a single swatch entry in a palette."""

    id: str
    label: str
    color: ColorDescriptor
    locked: bool = False


@dataclass
class ColorPalette:
    """Container for a named set of swatches."""

    id: str
    name: str
    swatches: list[PaletteSwatch] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def add_swatch(self, swatch: PaletteSwatch) -> None:
        self.swatches.append(swatch)
        self.updated_at = datetime.utcnow()

    def update_swatch(
        self,
        swatch_id: str,
        *,
        color: ColorDescriptor | None = None,
        label: str | None = None,
    ) -> None:
        for index, item in enumerate(self.swatches):
            if item.id == swatch_id:
                new_color = color if color is not None else item.color
                new_label = label if label is not None else item.label
                self.swatches[index] = PaletteSwatch(
                    id=item.id,
                    label=new_label,
                    color=new_color,
                    locked=item.locked,
                )
                self.updated_at = datetime.utcnow()
                return
        raise KeyError(f"Swatch {swatch_id} not found")

    def remove_swatch(self, swatch_id: str) -> None:
        before = len(self.swatches)
        self.swatches = [item for item in self.swatches if item.id != swatch_id]
        if len(self.swatches) == before:
            raise KeyError(f"Swatch {swatch_id} not found")
        self.updated_at = datetime.utcnow()

    def reorder(self, order: Iterable[str]) -> None:
        order_map = {swatch_id: idx for idx, swatch_id in enumerate(order)}
        self.swatches.sort(key=lambda item: order_map.get(item.id, len(order_map)))
        self.updated_at = datetime.utcnow()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "swatches": [
                {
                    "id": swatch.id,
                    "label": swatch.label,
                    "color": _descriptor_to_payload(swatch.color),
                    "locked": swatch.locked,
                }
                for swatch in self.swatches
            ],
            "tags": sorted(self.tags),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ColorPalette:
        swatches = [
            PaletteSwatch(
                id=item.get("id", SwatchId.CUSTOM.value),
                label=item.get("label", ""),
                color=_descriptor_from_payload(item.get("color", {})),
                locked=bool(item.get("locked", False)),
            )
            for item in data.get("swatches", [])
        ]
        tags = set(data.get("tags", []))
        created = _parse_datetime(data.get("created_at"))
        updated = _parse_datetime(data.get("updated_at"))
        return cls(
            id=str(data.get("id", DEFAULT_PALETTE_ID)),
            name=str(data.get("name", "Palette")),
            swatches=swatches,
            tags=tags,
            created_at=created,
            updated_at=updated,
        )


def _descriptor_to_payload(descriptor: ColorDescriptor) -> dict[str, Any]:
    return {
        "rgba": list(descriptor.rgba),
        "blend_mode": descriptor.blend_mode,
        "meta": descriptor.meta,
    }


def _descriptor_from_payload(payload: Mapping[str, Any]) -> ColorDescriptor:
    rgba = payload.get("rgba", [0.0, 0.0, 0.0, 1.0])
    blend_mode = payload.get("blend_mode", "normal")
    meta = payload.get("meta")
    if len(rgba) == 3:
        rgba = [*list(rgba), 1.0]
    return ColorDescriptor(
        rgba=tuple(float(c) for c in rgba), blend_mode=str(blend_mode), meta=meta
    )


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            logger.debug("datetime isoformat parse failed", exc_info=True)
    return datetime.utcnow()


__all__ = [
    "DEFAULT_PALETTE_ID",
    "ColorPalette",
    "PaletteSwatch",
    "SwatchId",
]
