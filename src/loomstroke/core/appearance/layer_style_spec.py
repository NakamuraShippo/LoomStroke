"""One layer style entry: stable id, kind, params, opacity, enabled (CT-21 ``LayerStyleSpec``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

__all__ = ["LayerStyleSpec"]


@dataclass(frozen=True, slots=True)
class LayerStyleSpec:
    style_id: str
    kind: str
    params: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    opacity: float = 1.0
    blend_mode: str = "normal"

    def with_params(self, params: Mapping[str, Any]) -> LayerStyleSpec:
        return replace(self, params=dict(params))

    def with_enabled(self, enabled: bool) -> LayerStyleSpec:
        return replace(self, enabled=bool(enabled))

    def to_dict(self) -> dict[str, Any]:
        return {
            "style_id": self.style_id,
            "kind": self.kind,
            "params": dict(self.params),
            "enabled": bool(self.enabled),
            "opacity": float(self.opacity),
            "blend_mode": self.blend_mode,
        }

    @classmethod
    def from_dict(cls, data: Any) -> LayerStyleSpec | None:
        if not isinstance(data, Mapping):
            return None
        style_id = data.get("style_id")
        kind = data.get("kind")
        if not isinstance(style_id, str) or not style_id or not isinstance(kind, str) or not kind:
            return None
        params = data.get("params")
        try:
            opacity = max(0.0, min(1.0, float(data.get("opacity", 1.0))))
        except (TypeError, ValueError):
            opacity = 1.0
        return cls(
            style_id,
            kind,
            dict(params) if isinstance(params, Mapping) else {},
            bool(data.get("enabled", True)),
            opacity,
            str(data.get("blend_mode") or "normal"),
        )
