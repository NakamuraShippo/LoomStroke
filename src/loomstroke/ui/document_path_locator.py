"""Finds the saved path of the current document (main window attribute) for locators."""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["DocumentPathLocator"]


class DocumentPathLocator:
    def __init__(self, window: Any | None = None) -> None:
        self._window = window

    def document_path(self) -> Path | None:
        window = self._window if self._window is not None else self._find_window()
        raw = getattr(window, "_document_path", None)
        if not raw:
            return None
        try:
            return Path(str(raw))
        except (TypeError, ValueError):
            return None

    def document_dir(self) -> Path | None:
        path = self.document_path()
        return path.parent if path is not None else None

    @staticmethod
    def _find_window() -> Any | None:
        try:
            from PySide6.QtWidgets import QApplication  # noqa: PLC0415
        except ImportError:  # pragma: no cover - headless builds
            return None
        app = QApplication.instance()
        if app is None:
            return None
        for widget in app.topLevelWidgets():
            if hasattr(widget, "_document_path") and hasattr(widget, "_get_document_service"):
                return widget
        return None
