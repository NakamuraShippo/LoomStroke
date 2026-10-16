"""Per-page assist collection stored under ``view_state["manga_drawing_assists"]`` (CT-14)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

from .drawing_assist import DrawingAssist

__all__ = ["DrawingAssistState"]


@dataclass(frozen=True, slots=True)
class DrawingAssistState:
    items: tuple[DrawingAssist, ...] = ()
    active_id: str | None = None
    schema_version: int = 1
    extra: Mapping[str, Any] = field(default_factory=dict)
    """Unknown top-level keys, preserved verbatim on write."""

    VIEW_STATE_KEY: ClassVar[str] = "manga_drawing_assists"
    SCHEMA_VERSION: ClassVar[int] = 1
    MAX_ITEMS: ClassVar[int] = 64

    def get(self, assist_id: str | None) -> DrawingAssist | None:
        if not assist_id:
            return None
        return next((item for item in self.items if item.id == assist_id), None)

    def active(self) -> DrawingAssist | None:
        assist = self.get(self.active_id)
        return assist if assist is not None and assist.enabled and assist.is_known else None

    def with_item(self, assist: DrawingAssist, *, activate: bool = False) -> DrawingAssistState:
        items = list(self.items)
        for index, item in enumerate(items):
            if item.id == assist.id:
                items[index] = assist
                break
        else:
            items.append(assist)
        active = assist.id if activate else self.active_id
        return replace(self, items=tuple(items[: self.MAX_ITEMS]), active_id=active)

    def without(self, assist_id: str) -> DrawingAssistState:
        items = tuple(item for item in self.items if item.id != assist_id)
        active = None if self.active_id == assist_id else self.active_id
        return replace(self, items=items, active_id=active)

    def with_active(self, assist_id: str | None) -> DrawingAssistState:
        return replace(self, active_id=assist_id if self.get(assist_id) else None)

    @property
    def is_empty(self) -> bool:
        return not self.items

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extra)
        payload.update(
            {
                "schema_version": self.SCHEMA_VERSION,
                "items": [item.to_dict() for item in self.items],
                "active_id": self.active_id,
            }
        )
        return payload

    @classmethod
    def from_dict(cls, data: Any) -> DrawingAssistState:
        if not isinstance(data, Mapping):
            return cls()
        raw_items = data.get("items", [])
        items: list[DrawingAssist] = []
        if isinstance(raw_items, list):
            for raw in raw_items:
                assist = DrawingAssist.from_dict(raw)
                if assist is not None:
                    items.append(assist)
        version = data.get("schema_version", 1)
        version = version if isinstance(version, int) and version >= 1 else 1
        active = data.get("active_id")
        extra = {
            key: value
            for key, value in data.items()
            if key not in {"schema_version", "items", "active_id"}
        }
        state = cls(tuple(items[: cls.MAX_ITEMS]), None, version, extra)
        return state.with_active(active if isinstance(active, str) else None)
