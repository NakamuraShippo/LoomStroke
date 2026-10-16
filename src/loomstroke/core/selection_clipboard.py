"""Selection clipboard data structures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SelectionPayload:
    """Selection payload for clipboard and transform operations."""

    image: Any
    mask: Any
    bounds: tuple[int, int, int, int]
    source_layer_id: str | None = None
    dpi: int | None = None


class SelectionClipboard:
    """In-memory clipboard for selection payloads."""

    def __init__(self) -> None:
        self._payload: SelectionPayload | None = None

    def set(self, payload: SelectionPayload | None) -> None:
        self._payload = payload

    def get(self) -> SelectionPayload | None:
        return self._payload

    def is_empty(self) -> bool:
        return self._payload is None
