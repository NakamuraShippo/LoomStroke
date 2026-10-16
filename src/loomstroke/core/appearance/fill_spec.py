"""Non-destructive fill definition stored on a raster layer (CT-21 ``FillSpec``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

__all__ = ["FillSpec"]


@dataclass(frozen=True, slots=True)
class FillSpec:
    fill_id: str
    kind: str
    params: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True
    version: int = 1

    SCHEMA_VERSION: ClassVar[int] = 1

    def with_params(self, params: Mapping[str, Any]) -> FillSpec:
        return replace(self, params=dict(params), version=self.version + 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "fill_id": self.fill_id,
            "kind": self.kind,
            "params": dict(self.params),
            "enabled": bool(self.enabled),
            "version": int(self.version),
        }

    @classmethod
    def from_dict(cls, data: Any) -> FillSpec | None:
        if not isinstance(data, Mapping):
            return None
        fill_id = data.get("fill_id")
        kind = data.get("kind")
        if not isinstance(fill_id, str) or not fill_id or not isinstance(kind, str) or not kind:
            return None
        params = data.get("params")
        version = data.get("version", 1)
        return cls(
            fill_id,
            kind,
            dict(params) if isinstance(params, Mapping) else {},
            bool(data.get("enabled", True)),
            version if isinstance(version, int) and version >= 1 else 1,
        )
