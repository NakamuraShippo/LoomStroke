"""Refresh brush panel layout after dock changes."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer

from .brush_manager_panel import BrushManagerPanel


class _DockLayoutWatcher(QObject):

    def __init__(self, panel: BrushManagerPanel) -> None:

        super().__init__(panel)

        self._panel: BrushManagerPanel | None = panel

    def set_panel(self, panel: BrushManagerPanel) -> None:

        self._panel = panel

        self.setParent(panel)

    def _refresh(self) -> None:

        panel = self._panel

        if panel is None:

            return

        panel.refresh_button_layout()

    def on_dock_changed(self, *_args) -> None:

        QTimer.singleShot(0, self._refresh)

    def eventFilter(self, obj, event) -> bool:

        if event.type() in (QEvent.Type.Resize, QEvent.Type.MouseButtonRelease):

            QTimer.singleShot(0, self._refresh)

        return False
