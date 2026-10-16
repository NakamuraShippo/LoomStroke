"""Canvas-relative placement and persistence for the floating filter settings."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QTimer, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid as is_valid

from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names

logger = logging.getLogger(__name__)

PANEL_POSITION_KEY = "ui/effect_pen/settings_panel_position"
_MARGIN = 16


def saved_offset(value: Any) -> QPoint | None:
    """Accept finite, bounded logical coordinates from the settings service."""
    if (
        not isinstance(value, Mapping)
        or type(value.get("version")) is not int
        or value.get("version") != 1
    ):
        return None
    coordinates = []
    for key in ("x", "y"):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            return None
        if abs(number) > 1_000_000 or not math.isfinite(number):
            return None
        coordinates.append(round(number))
    return QPoint(*coordinates)


def clamp_top_left(point: QPoint, size: Any, bounds: QRect) -> QPoint:
    """Keep a frame in bounds, or its leading edge when it cannot fully fit."""
    if bounds.isEmpty():
        return QPoint(point)
    max_x = max(bounds.left(), bounds.right() - size.width() + 1)
    max_y = max(bounds.top(), bounds.bottom() - size.height() + 1)
    return QPoint(
        min(max(point.x(), bounds.left()), max_x),
        min(max(point.y(), bounds.top()), max_y),
    )


class FilterPenPanelPosition(QObject):
    """Track a Tool window in canvas logical pixels without changing tool presets."""

    def __init__(
        self,
        dialog: QWidget,
        settings: Any,
        anchor: QWidget,
        *,
        key: str = PANEL_POSITION_KEY,
        debounce_ms: int = 200,
    ) -> None:
        super().__init__(dialog)
        self._dialog = dialog
        self._settings = settings
        self._anchor = anchor
        self._key = key
        self._offset: QPoint | None = None
        self._disposed = False
        self._restoring = True
        self._shown = dialog.isVisible()
        self._syncing = False
        self._dirty = False
        self._watched: list[QWidget] = []
        self._screen_connections: list[Any] = []
        self._watched_screens: list[Any] = []
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(max(0, debounce_ms))
        self._save_timer.timeout.connect(self.flush)
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self._sync_after_layout)
        dialog.installEventFilter(self)
        ancestor: QWidget | None = anchor
        while ancestor is not None:
            if ancestor is not dialog:
                ancestor.installEventFilter(self)
                self._watched.append(ancestor)
            ancestor = ancestor.parentWidget()
        app = QGuiApplication.instance()
        if app is not None:
            self._connect_screen(app.screenAdded)
            self._connect_screen(app.screenRemoved)
        self._watch_screens()

    def restore(self) -> bool:
        """Restore before showing, then correct native frame margins after Show."""
        if not self._usable():
            return False
        self.flush()
        self._shown = self._dialog.isVisible()
        self._restoring = True
        try:
            value = self._settings.get(self._key, {})
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.warning("Could not read Filter Pen panel position", exc_info=True)
            value = None
        self._offset = saved_offset(value)
        restored = self._offset is not None
        if self._offset is None:
            self._offset = self._default_offset()
        self._sync_position()
        self._sync_timer.start(0)
        return restored

    @Slot()
    def flush(self) -> None:
        """Persist only user moves; restore and parent movement do not write."""
        if (
            getattr(self, "_disposed", True)
            or not is_valid(self)
            or not self._dirty
            or self._offset is None
        ):
            return
        self._save_timer.stop()
        value = {"version": 1, "x": self._offset.x(), "y": self._offset.y()}
        try:
            self._settings.set(self._key, value)
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.warning("Could not save Filter Pen panel position", exc_info=True)
            return
        self._dirty = False

    def dispose(self) -> None:
        """Release subscriptions while the dialog and its helper are still valid."""
        if getattr(self, "_disposed", True) or not is_valid(self):
            return
        self.flush()
        self._disposed = True
        self._save_timer.stop()
        self._sync_timer.stop()
        if is_valid(self._dialog):
            self._dialog.removeEventFilter(self)
        for widget in self._watched:
            if is_valid(widget):
                widget.removeEventFilter(self)
        self._watched.clear()
        for connection in self._screen_connections:
            if not QObject.disconnect(connection):
                logger.debug("Filter Pen screen subscription already removed")
        self._screen_connections.clear()
        self._watched_screens.clear()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if not self._usable():
            return False
        kind = event.type()
        if obj is self._dialog:
            if kind in (QEvent.Type.Hide, QEvent.Type.Close):
                self.flush()
                self._request_underlay_refresh()
            elif kind == QEvent.Type.Show:
                self._shown = True
                self._restoring = True
                if self._offset is None:
                    self.restore()
                self._sync_timer.start(0)
            elif kind == QEvent.Type.Move:
                if not self._syncing and self._shown and self._dialog.isVisible():
                    self._offset = self._anchor.mapFromGlobal(
                        self._dialog.frameGeometry().topLeft()
                    )
                    self._sync_position()
                    self._dirty = True
                    self._save_timer.start()
                self._request_underlay_refresh()
            elif kind == QEvent.Type.Resize and not self._syncing:
                self._sync_timer.start(0)
        elif kind in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show):
            self._sync_timer.start(0)
        elif kind in (QEvent.Type.Hide, QEvent.Type.Close):
            self.flush()
        return False

    def _usable(self) -> bool:
        return bool(
            not getattr(self, "_disposed", True)
            and is_valid(self)
            and is_valid(self._dialog)
            and is_valid(self._anchor)
        )

    def _default_offset(self) -> QPoint:
        size = self._dialog.frameGeometry().size()
        return QPoint(max(_MARGIN, self._anchor.width() - size.width() - _MARGIN), _MARGIN)

    def _available_bounds(self, target: QPoint) -> QRect:
        rect = QRect(self._anchor.mapToGlobal(QPoint()), self._anchor.size())
        inset = rect.adjusted(_MARGIN, _MARGIN, -_MARGIN, -_MARGIN)
        if not inset.isEmpty():
            rect = inset
        size = self._dialog.frameGeometry().size()
        center = target + QPoint(size.width() // 2, size.height() // 2)
        screen = QGuiApplication.screenAt(center) or self._anchor.screen()
        if screen is not None:
            on_screen = rect.intersected(screen.availableGeometry())
            if not on_screen.isEmpty():
                return on_screen
            return screen.availableGeometry()
        return rect

    def _sync_position(self) -> None:
        if not self._usable() or self._offset is None:
            return
        target = self._anchor.mapToGlobal(self._offset)
        bounds = self._available_bounds(target)
        self._fit_size(bounds)
        frame_size = self._dialog.frameGeometry().size()
        target = clamp_top_left(target, frame_size, bounds)
        self._offset = self._anchor.mapFromGlobal(target)
        # QWidget.move and frameGeometry may differ on a decorated Tool window.
        correction = self._dialog.frameGeometry().topLeft() - self._dialog.pos()
        self._syncing = True
        try:
            self._dialog.move(target - correction)
        finally:
            self._syncing = False
        self._request_underlay_refresh()

    def _fit_size(self, bounds: QRect) -> None:
        """Use the dialog's scroll area when the available canvas has shrunk."""
        frame = self._dialog.frameGeometry().size()
        chrome_width = max(0, frame.width() - self._dialog.width())
        chrome_height = max(0, frame.height() - self._dialog.height())
        width = min(self._dialog.width(), max(1, bounds.width() - chrome_width))
        height = min(self._dialog.height(), max(1, bounds.height() - chrome_height))
        self._syncing = True
        try:
            self._dialog.resize(width, height)
        finally:
            self._syncing = False

    @Slot()
    def _sync_after_layout(self) -> None:
        if not self._usable():
            return
        if self._dialog.isVisible():
            self._sync_position()
            self._restoring = False

    def _request_underlay_refresh(self) -> None:
        if not self._usable():
            return
        refresh = getattr(self._anchor, "request_native_surface_refresh", None)
        if callable(refresh):
            refresh(force_full_recompose=False)
        else:
            self._anchor.update()

    def _connect_screen(self, signal: Any) -> None:
        self._screen_connections.append(signal.connect(self._screen_changed))

    def _watch_screens(self) -> None:
        for screen in QGuiApplication.screens():
            if any(screen is previous for previous in self._watched_screens):
                continue
            self._watched_screens.append(screen)
            for signal in (screen.availableGeometryChanged, screen.geometryChanged):
                self._connect_screen(signal)

    @Slot()
    def _screen_changed(self) -> None:
        if self._usable():
            self._watch_screens()
            self._sync_timer.start(0)


# Compatibility aliases for existing imports.
EffectPenPanelPosition = FilterPenPanelPosition


_bind_legacy_names(__name__, {"EffectPenPanelPosition": "FilterPenPanelPosition"})
