"""One immutable drawing assist: type, page-local geometry, options (CT-14 data model)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from .assist_type import is_known_type

__all__ = ["DrawingAssist", "Point"]

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class DrawingAssist:
    id: str
    type: str
    enabled: bool = True
    locked: bool = False
    geometry: Mapping[str, Any] = field(default_factory=dict)
    options: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_known(self) -> bool:
        return is_known_type(self.type)

    def point(self, key: str) -> Point | None:
        raw = self.geometry.get(key)
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return None
        try:
            x, y = float(raw[0]), float(raw[1])
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        return (x, y)

    def points(self, key: str) -> tuple[Point, ...]:
        raw = self.geometry.get(key)
        if not isinstance(raw, (list, tuple)):
            return ()
        found: list[Point] = []
        for item in raw:
            probe = replace(self, geometry={"_": item})
            point = probe.point("_")
            if point is not None:
                found.append(point)
        return tuple(found)

    def number(self, key: str, default: float, *, source: str = "geometry") -> float:
        store = self.geometry if source == "geometry" else self.options
        raw = store.get(key, default)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    def option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)

    def with_geometry(self, **updates: Any) -> DrawingAssist:
        merged = dict(self.geometry)
        merged.update(updates)
        return replace(self, geometry=merged)

    def with_options(self, **updates: Any) -> DrawingAssist:
        merged = dict(self.options)
        merged.update(updates)
        return replace(self, options=merged)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "enabled": bool(self.enabled),
            "locked": bool(self.locked),
            "geometry": _jsonable(self.geometry),
            "options": _jsonable(self.options),
        }

    @classmethod
    def from_dict(cls, data: Any) -> DrawingAssist | None:
        if not isinstance(data, Mapping):
            return None
        assist_id = data.get("id")
        kind = data.get("type")
        if not isinstance(assist_id, str) or not assist_id or not isinstance(kind, str) or not kind:
            return None
        geometry = data.get("geometry", {})
        options = data.get("options", {})
        return cls(
            id=assist_id,
            type=kind,
            enabled=bool(data.get("enabled", True)),
            locked=bool(data.get("locked", False)),
            geometry=dict(geometry) if isinstance(geometry, Mapping) else {},
            options=dict(options) if isinstance(options, Mapping) else {},
        )


def _jsonable(mapping: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        if isinstance(value, tuple):
            out[key] = [list(v) if isinstance(v, tuple) else v for v in value]
        else:
            out[key] = value
    return out
