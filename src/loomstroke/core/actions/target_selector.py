"""Declarative target of a macro step (CT-20 §8.1 ``TargetSelector``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from .action_descriptor import TargetKind

__all__ = ["SelectorKind", "TargetSelector"]

SelectorKind = Literal[
    "active_layer",
    "layer_by_id",
    "layer_by_name",
    "all_layers",
    "active_page",
    "page_by_id",
    "document",
    "none",
]


@dataclass(frozen=True, slots=True)
class TargetSelector:
    kind: SelectorKind
    value: str | None = None

    _TARGET_KINDS: ClassVar[dict[str, TargetKind]] = {
        "active_layer": "layer",
        "layer_by_id": "layer",
        "layer_by_name": "layer",
        "all_layers": "layer",
        "active_page": "page",
        "page_by_id": "page",
        "document": "document",
        "none": "none",
    }

    @property
    def target_kind(self) -> TargetKind:
        return self._TARGET_KINDS[self.kind]

    @property
    def needs_value(self) -> bool:
        return self.kind in {"layer_by_id", "layer_by_name", "page_by_id"}

    def describe(self) -> str:
        return f"{self.kind}:{self.value}" if self.value else self.kind

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": self.kind}
        if self.value is not None:
            data["value"] = self.value
        return data

    @classmethod
    def from_dict(cls, data: Any) -> TargetSelector | None:
        if not isinstance(data, dict):
            return None
        kind = data.get("kind")
        if kind not in cls._TARGET_KINDS:
            return None
        value = data.get("value")
        if value is not None and not isinstance(value, str):
            return None
        selector = cls(kind, value)
        if selector.needs_value and not value:
            return None
        return selector
