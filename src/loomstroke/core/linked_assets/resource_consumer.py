"""One layer that shows a resource (derived, never persisted in the record)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ResourceConsumer"]


@dataclass(frozen=True, slots=True)
class ResourceConsumer:
    page_id: str
    layer_id: str
    layer_name: str = ""

    @property
    def label(self) -> str:
        return self.layer_name or self.layer_id
