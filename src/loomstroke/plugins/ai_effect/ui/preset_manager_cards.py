"""Preset manager card construction helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QWidget


class PresetCardFactory:
    """Builds preset item widgets and wires their action signals."""

    def __init__(
        self,
        *,
        parent: QWidget,
        card_cls: Callable[[Any, QWidget | None], Any],
        list_row_cls: Callable[[Any, QWidget | None], Any],
        compact_card_cls: Callable[[Any, QWidget | None], Any],
        apply_handler: Callable[[str], None],
        edit_handler: Callable[[str], None],
        delete_handler: Callable[[str], None],
    ) -> None:
        self._parent = parent
        self._card_cls = card_cls
        self._list_row_cls = list_row_cls
        self._compact_card_cls = compact_card_cls
        self._apply_handler = apply_handler
        self._edit_handler = edit_handler
        self._delete_handler = delete_handler

    def create(self, preset: Any, view_mode: str) -> Any:
        if view_mode == "list":
            item = self._list_row_cls(preset, self._parent)
        elif view_mode == "compact":
            item = self._compact_card_cls(preset, self._parent)
        else:
            item = self._card_cls(preset, self._parent)
        item.apply_requested.connect(self._apply_handler)
        item.edit_requested.connect(self._edit_handler)
        item.delete_requested.connect(self._delete_handler)
        return item


__all__ = ["PresetCardFactory"]
