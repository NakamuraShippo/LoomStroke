"""Where one font request is used (CT-25 Font Diagnostics rows)."""

from __future__ import annotations

from dataclasses import dataclass

from .font_request import FontRequest

__all__ = ["FontUsage"]

ObjectRef = tuple[str, str, str]  # (page_id, layer_id, object_id)


@dataclass(frozen=True, slots=True)
class FontUsage:
    request: FontRequest
    object_refs: tuple[ObjectRef, ...] = ()
    char_count: int = 0
    characters: frozenset[str] = frozenset()

    @property
    def page_ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for page_id, _layer, _obj in self.object_refs:
            if page_id not in seen:
                seen.append(page_id)
        return tuple(seen)

    @property
    def object_count(self) -> int:
        return len(self.object_refs)
