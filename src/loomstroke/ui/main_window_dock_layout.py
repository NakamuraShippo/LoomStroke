"""Dock layout, sizing, and spacer coordination for MainWindow."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any, cast

from PySide6.QtCore import QCoreApplication, QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDockWidget, QMenu, QWidget

from ..host_api import UILayoutSnapshot
from ._dock_safe import is_valid
from .floating_dock_geometry import install_floating_dock_guard

logger = logging.getLogger(__name__)

_DOCK_TRACKING_PROP = "sr_dock_tracking"
_DOCK_UID_PROP = "sr_dock_uid"
_DOCK_FLOAT_SIZE_PROP = "sr_last_floating_size"
_DOCK_FLOAT_TIMER_PROP = "sr_floating_size_timer"
_DOCK_DRAGGING_PROP = "sr_floating_dragging"
_DOCK_DRAGGING_RELEASE_PROP = "sr_floating_drag_release"
_DOCK_RESTORING_SIZE_PROP = "sr_restoring_float_size"
_DOCK_PREDOCK_SIZE_PROP = "sr_predock_float_size"
_DOCK_DOCKED_SIZE_PROP = "sr_last_docked_size"
_DOCK_DOCKED_AREA_PROP = "sr_last_docked_area"
_DOCK_DEFAULT_AREA_PROP = "sr_default_area"
_DOCK_DEFAULT_VISIBLE_PROP = "sr_default_visible"
_DOCK_SPACER_PROP = "sr_dock_spacer"
_DOCK_SPACER_FOR_PROP = "sr_spacer_for_uid"
_PANEL_MIN_WIDTH = 100
_PANEL_MIN_HEIGHT = 50
_PANEL_MIN_SIZE = QSize(_PANEL_MIN_WIDTH, _PANEL_MIN_HEIGHT)
_MAX_QWIDGET_SIZE = 16777215
_DOCK_AREAS = (
    Qt.DockWidgetArea.LeftDockWidgetArea,
    Qt.DockWidgetArea.RightDockWidgetArea,
    Qt.DockWidgetArea.TopDockWidgetArea,
    Qt.DockWidgetArea.BottomDockWidgetArea,
)
_AREA_NAMES = {
    Qt.DockWidgetArea.LeftDockWidgetArea: "Left",
    Qt.DockWidgetArea.RightDockWidgetArea: "Right",
    Qt.DockWidgetArea.TopDockWidgetArea: "Top",
    Qt.DockWidgetArea.BottomDockWidgetArea: "Bottom",
}
_VERT_AREAS = (
    Qt.DockWidgetArea.LeftDockWidgetArea,
    Qt.DockWidgetArea.RightDockWidgetArea,
)


def _dock_widgets(window: Any) -> list[QDockWidget]:
    return cast(list[QDockWidget], window.findChildren(QDockWidget))


class MainWindowDockSizeCacheController:
    """Persist and restore floating/docked dock sizes."""

    def __init__(
        self,
        window: Any,
        *,
        owner: Any,
        single_shot: Callable[[int, Callable[[], None]], None],
    ) -> None:
        self._window = window
        self._owner = owner
        self._single_shot = single_shot

    def load_dock_sizes(self, key: str) -> None:
        """Load saved dock sizes from settings into caches."""
        raw = self._window._settings.get(key)
        if not isinstance(raw, dict):
            return
        for name, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            fw = entry.get("floating")
            if isinstance(fw, list) and len(fw) == 2:
                self._window._floating_size_cache[f"name:{name}"] = QSize(int(fw[0]), int(fw[1]))
            dw = entry.get("docked")
            if isinstance(dw, list) and len(dw) == 2:
                self._window._docked_size_cache[name] = [int(dw[0]), int(dw[1])]

    def save_dock_sizes(self, key: str) -> None:
        """Persist current dock sizes to settings."""
        from loomstroke.platform.launch_options import ai_features_enabled  # noqa: PLC0415

        if not ai_features_enabled():
            return
        data: dict[str, dict[str, list[int]]] = {}
        for dock in _dock_widgets(self._window):
            if self._owner.is_spacer_dock(dock):
                continue
            persist_key = self._owner.dock_persist_key(dock)
            if not persist_key:
                continue
            entry: dict[str, list[int]] = {}
            fs = dock.property(_DOCK_PREDOCK_SIZE_PROP)
            if not isinstance(fs, QSize):
                fs = dock.property(_DOCK_FLOAT_SIZE_PROP)
            if not isinstance(fs, QSize):
                fs = self._window._floating_size_cache.get(f"name:{persist_key}")
            if isinstance(fs, QSize) and fs.width() > 0 and fs.height() > 0:
                entry["floating"] = [fs.width(), fs.height()]
            ds = dock.property(_DOCK_DOCKED_SIZE_PROP)
            if isinstance(ds, QSize) and ds.width() > 0 and ds.height() > 0:
                entry["docked"] = [ds.width(), ds.height()]
            elif persist_key in self._window._docked_size_cache:
                entry["docked"] = self._window._docked_size_cache[persist_key]
            if entry:
                data[persist_key] = entry
        self._window._settings.set(key, data)

    def mark_dock_sizes_dirty(self) -> None:
        """Schedule a deferred save of dock sizes."""
        if self._window._dock_sizes_dirty:
            return
        self._window._dock_sizes_dirty = True
        self._single_shot(2000, self._window._flush_dock_sizes)

    def flush_dock_sizes(self, key: str) -> None:
        if not self._window._dock_sizes_dirty:
            return
        self._window._dock_sizes_dirty = False
        self.save_dock_sizes(key)

    def floating_cache_key(self, dock: QDockWidget) -> str:
        name = dock.objectName()
        if name:
            return f"name:{name}"
        return f"uid:{self._owner.dock_uid(dock)}"

    def last_floating_size(self, dock: QDockWidget) -> QSize | None:
        value = dock.property(_DOCK_FLOAT_SIZE_PROP)
        if isinstance(value, QSize):
            return value
        return cast(
            QSize | None, self._window._floating_size_cache.get(self.floating_cache_key(dock))
        )

    def record_floating_size(self, dock: QDockWidget) -> None:
        if not dock.isFloating():
            return
        if dock.property(_DOCK_DRAGGING_PROP):
            return
        if dock.property(_DOCK_RESTORING_SIZE_PROP):
            return
        if QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton:
            return
        size = dock.size()
        prev = dock.property(_DOCK_FLOAT_SIZE_PROP)
        dock.setProperty(_DOCK_FLOAT_SIZE_PROP, size)
        self._window._floating_size_cache[self.floating_cache_key(dock)] = size
        if isinstance(prev, QSize) and prev == size:
            return
        self._owner.log_dock(dock, f"record_floating_size {size.width()}x{size.height()}")
        self.mark_dock_sizes_dirty()

    def force_record_floating_size(self, dock: QDockWidget) -> None:
        if not dock.isFloating():
            return
        if dock.property(_DOCK_RESTORING_SIZE_PROP):
            return
        size = dock.size()
        dock.setProperty(_DOCK_FLOAT_SIZE_PROP, size)
        self._window._floating_size_cache[self.floating_cache_key(dock)] = size
        self._owner.log_dock(dock, f"force_record_floating_size {size.width()}x{size.height()}")
        self.mark_dock_sizes_dirty()

    def snapshot_docked_size(self, dock: QDockWidget) -> None:
        """Save the dock's current size and area while it is docked."""
        if dock.isFloating():
            return
        area = self._window.dockWidgetArea(dock)
        size = dock.size()
        dock.setProperty(_DOCK_DOCKED_SIZE_PROP, size)
        dock.setProperty(_DOCK_DOCKED_AREA_PROP, int(cast("Any", area).value))
        self._owner.log_dock(
            dock,
            f"snapshot_docked_size {size.width()}x{size.height()} area={area}",
        )
        self.mark_dock_sizes_dirty()

    def apply_docked_size(self, dock: QDockWidget) -> None:
        """Restore previously saved docked size via resizeDocks."""
        if not is_valid(dock) or dock.isFloating():
            return
        size = dock.property(_DOCK_DOCKED_SIZE_PROP)
        if not isinstance(size, QSize):
            return
        area = self._window.dockWidgetArea(dock)
        if not self._owner.visible_real_docks_in_area(area, exclude=dock):
            return
        if area in _VERT_AREAS:
            self._window.resizeDocks([dock], [size.width()], Qt.Orientation.Horizontal)
        elif area in (
            Qt.DockWidgetArea.TopDockWidgetArea,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        ):
            self._window.resizeDocks([dock], [size.height()], Qt.Orientation.Vertical)
        self._owner.log_dock(dock, f"apply_docked_size {size.width()}x{size.height()}")

    def schedule_drag_finalize(self, dock: QDockWidget) -> None:
        if not is_valid(dock) or self._owner.is_spacer_dock(dock):
            return
        if dock.property(_DOCK_DRAGGING_RELEASE_PROP):
            return
        dock.setProperty(_DOCK_DRAGGING_RELEASE_PROP, True)
        self._owner.log_dock(dock, "drag_finalize_scheduled")

        def finalize_drag_release(d: QDockWidget = dock) -> None:
            self._window._finalize_drag_release(d)

        self._single_shot(140, finalize_drag_release)

    def finalize_drag_release(self, dock: QDockWidget) -> None:
        if not is_valid(dock) or self._owner.is_spacer_dock(dock):
            return
        dock.setProperty(_DOCK_DRAGGING_RELEASE_PROP, False)
        dock.setProperty(_DOCK_DRAGGING_PROP, False)
        self._owner.log_dock(dock, "drag_finalize")
        if dock.isFloating():
            self.force_record_floating_size(dock)
            self._owner.schedule_collapse_empty_dock_areas()

    def start_floating_size_timer(self, dock: QDockWidget) -> None:
        timer = dock.property(_DOCK_FLOAT_TIMER_PROP)
        if isinstance(timer, QTimer):
            return
        timer = QTimer(dock)
        timer.setInterval(500)

        def record_floating_size(d: QDockWidget = dock) -> None:
            self._window._record_floating_size(d)

        timer.timeout.connect(record_floating_size)
        timer.start()
        dock.setProperty(_DOCK_FLOAT_TIMER_PROP, timer)

    def stop_floating_size_timer(self, dock: QDockWidget) -> None:
        timer = dock.property(_DOCK_FLOAT_TIMER_PROP)
        if isinstance(timer, QTimer):
            timer.stop()
            timer.deleteLater()
        dock.setProperty(_DOCK_FLOAT_TIMER_PROP, None)

    def apply_floating_size_retry(self, dock: QDockWidget, size: QSize) -> None:
        self._owner.log_dock(dock, f"apply_floating_size_retry {size.width()}x{size.height()}")
        dock.setProperty(_DOCK_RESTORING_SIZE_PROP, True)
        for ms in (0, 80, 200, 400):

            def apply_size(d: QDockWidget = dock, s: QSize = size) -> None:
                self._window._apply_floating_size(d, s)

            self._single_shot(ms, apply_size)

        def clear_restoring_flag(d: QDockWidget = dock) -> None:
            d.setProperty(_DOCK_RESTORING_SIZE_PROP, False)

        self._single_shot(500, clear_restoring_flag)

    def apply_floating_size(self, dock: QDockWidget, size: QSize) -> None:
        if not is_valid(dock) or not dock.isFloating():
            return
        if size.width() < 1 or size.height() < 1:
            return
        self._owner.log_dock(dock, f"apply_floating_size {size.width()}x{size.height()}")
        self._owner.apply_panel_min_size(dock)
        dock.setMaximumSize(_MAX_QWIDGET_SIZE, _MAX_QWIDGET_SIZE)
        dock.resize(size)


class MainWindowDockSpacerController:
    """Manage transient spacer docks and empty-area collapse."""

    def __init__(
        self,
        window: Any,
        *,
        owner: Any,
        single_shot: Callable[[int, Callable[[], None]], None],
    ) -> None:
        self._window = window
        self._owner = owner
        self._single_shot = single_shot

    def is_spacer_dock(self, dock: QDockWidget) -> bool:
        return bool(dock.property(_DOCK_SPACER_PROP))

    def dock_uid(self, dock: QDockWidget) -> str:
        uid = dock.property(_DOCK_UID_PROP)
        if isinstance(uid, str) and uid:
            return uid
        uid = str(id(dock))
        dock.setProperty(_DOCK_UID_PROP, uid)
        return uid

    def apply_empty_area_sizing(self, dock: QDockWidget) -> None:
        if not is_valid(dock) or dock.isFloating() or self.is_spacer_dock(dock):
            return
        area = self._window.dockWidgetArea(dock)
        if area not in _DOCK_AREAS:
            return
        if self.visible_real_docks_in_area(area, exclude=dock):
            self._owner.log_dock(dock, "empty_area_sizing skip: other docks present")
            self.remove_spacers_in_area(area)
            return

        size = self._owner.last_floating_size(dock) or dock.size()
        if area in _VERT_AREAS:
            orientation = Qt.Orientation.Vertical
            target = size.height()
        else:
            orientation = Qt.Orientation.Horizontal
            target = size.width()
        if target < 1:
            return

        uid = self.dock_uid(dock)
        spacer = self.find_spacer_for_uid(uid)
        if spacer is None:
            spacer = self.create_spacer_dock(uid, area)
            self._window.addDockWidget(area, spacer)
            self._window.splitDockWidget(dock, spacer, orientation)
            self._owner.log_dock(dock, "empty_area_sizing spacer_created")

        def _apply_resize() -> None:
            if not is_valid(dock) or not is_valid(spacer) or dock.isFloating():
                return
            if orientation == Qt.Orientation.Vertical:
                total = dock.height() + spacer.height()
            else:
                total = dock.width() + spacer.width()
            if total <= 1:
                return
            desired = max(1, min(target, total - 1))
            self._owner.log_dock(
                dock,
                f"empty_area_sizing resizeDocks target={target} total={total} desired={desired}",
            )
            self._window.resizeDocks([dock, spacer], [desired, total - desired], orientation)

        self._single_shot(0, _apply_resize)

    def visible_real_docks_in_area(
        self,
        area: Qt.DockWidgetArea,
        *,
        exclude: QDockWidget | None = None,
    ) -> list[QDockWidget]:
        docks: list[QDockWidget] = []
        for dock in _dock_widgets(self._window):
            if dock is exclude:
                continue
            if self.is_spacer_dock(dock):
                continue
            if dock.isFloating() or not dock.isVisible():
                continue
            if self._window.dockWidgetArea(dock) != area:
                continue
            docks.append(dock)
        return docks

    def find_spacer_for_uid(self, uid: str) -> QDockWidget | None:
        for dock in _dock_widgets(self._window):
            if not self.is_spacer_dock(dock):
                continue
            if dock.property(_DOCK_SPACER_FOR_PROP) == uid:
                return dock
        return None

    def remove_spacer_for_uid(self, uid: str) -> None:
        spacer = self.find_spacer_for_uid(uid)
        if spacer is None:
            return
        self.remove_spacer_dock(spacer, reason="remove_spacer_for_uid")

    def remove_spacers_in_area(self, area: Qt.DockWidgetArea) -> None:
        for dock in _dock_widgets(self._window):
            if not self.is_spacer_dock(dock):
                continue
            if self._window.dockWidgetArea(dock) != area:
                continue
            self.remove_spacer_dock(dock, reason="remove_spacer_in_area")

    def remove_spacer_dock(self, dock: QDockWidget, *, reason: str) -> None:
        with suppress(Exception):
            self._owner.log_dock(dock, reason)
            self._window.removeDockWidget(dock)
            dock.hide()
            dock.setParent(None)
            dock.deleteLater()

    def spacer_docks(self) -> list[QDockWidget]:
        return [
            dock
            for dock in _dock_widgets(self._window)
            if is_valid(dock) and self.is_spacer_dock(dock)
        ]

    def real_dock_for_uid(self, uid: str) -> QDockWidget | None:
        for dock in _dock_widgets(self._window):
            if not is_valid(dock) or self.is_spacer_dock(dock):
                continue
            if dock.property(_DOCK_UID_PROP) == uid:
                return dock
        return None

    def prune_stale_spacer_docks(self) -> bool:
        """Remove spacer docks whose owning real dock no longer occupies the area."""
        removed = False
        for spacer in self.spacer_docks():
            uid = spacer.property(_DOCK_SPACER_FOR_PROP)
            owner = self.real_dock_for_uid(uid) if isinstance(uid, str) else None
            if (
                owner is None
                or owner.isFloating()
                or not owner.isVisible()
                or self._window.dockWidgetArea(owner) != self._window.dockWidgetArea(spacer)
            ):
                self.remove_spacer_dock(spacer, reason="prune_stale_spacer")
                removed = True
        return removed

    def clear_transient_dock_spacers(self) -> bool:
        """Drop internal spacer docks before persisting or exporting layouts."""
        removed = False
        for spacer in self.spacer_docks():
            self.remove_spacer_dock(spacer, reason="clear_transient_spacer")
            removed = True
        return removed

    def hidden_real_docks_in_area(self, area: Qt.DockWidgetArea) -> list[QDockWidget]:
        docks: list[QDockWidget] = []
        for dock in _dock_widgets(self._window):
            if not is_valid(dock) or self.is_spacer_dock(dock):
                continue
            if dock.isFloating() or dock.isVisible():
                continue
            if self._window.dockWidgetArea(dock) != area:
                continue
            docks.append(dock)
        return docks

    def dock_area_from_saved_property(self, dock: QDockWidget) -> Qt.DockWidgetArea:
        area_val = dock.property(_DOCK_DOCKED_AREA_PROP)
        if isinstance(area_val, int):
            with suppress(TypeError, ValueError):
                area = Qt.DockWidgetArea(area_val)
                if area != Qt.DockWidgetArea.NoDockWidgetArea:
                    return area
        return Qt.DockWidgetArea.RightDockWidgetArea

    def dock_area_from_default_property(self, dock: QDockWidget) -> Qt.DockWidgetArea:
        area_val = dock.property(_DOCK_DEFAULT_AREA_PROP)
        if isinstance(area_val, int):
            with suppress(TypeError, ValueError):
                area = Qt.DockWidgetArea(area_val)
                if area != Qt.DockWidgetArea.NoDockWidgetArea:
                    return area
        return self.dock_area_from_saved_property(dock)

    def ensure_dock_attached_for_show(self, dock: QDockWidget) -> None:
        if dock.isFloating():
            return
        cur_area = self._window.dockWidgetArea(dock)
        if cur_area != Qt.DockWidgetArea.NoDockWidgetArea:
            return
        self._window.addDockWidget(self.dock_area_from_saved_property(dock), dock)

    def enable_dock_collapse(self) -> None:
        self._window._dock_collapse_ready = True

    def schedule_collapse_empty_dock_areas(self) -> None:
        """Schedule a deferred collapse check, coalescing multiple requests."""
        if not getattr(self._window, "_dock_collapse_ready", False):
            return
        if getattr(self._window, "_collapse_cooldown", False):
            return
        timer = getattr(self._window, "_collapse_timer", None)
        if timer is None:
            timer = QTimer(self._window)
            timer.setSingleShot(True)
            timer.setInterval(200)
            timer.timeout.connect(self._window._collapse_empty_dock_areas)
            self._window._collapse_timer = timer
        timer.start()

    def clear_collapse_cooldown(self) -> None:
        self._window._collapse_cooldown = False

    def collapse_empty_dock_areas(self) -> None:
        """Redistribute space in dock areas after docks are floated."""
        if getattr(self._window, "_collapsing_areas", False):
            return
        self._window._collapsing_areas = True
        try:
            self.prune_stale_spacer_docks()
            need_layout_request = False

            for area in _DOCK_AREAS:
                visible = self.visible_real_docks_in_area(area)
                if visible:
                    orient = (
                        Qt.Orientation.Vertical
                        if area in _VERT_AREAS
                        else Qt.Orientation.Horizontal
                    )
                    with suppress(Exception):
                        self._window.resizeDocks(visible, [10000] * len(visible), orient)
                    continue

                for dock in self.hidden_real_docks_in_area(area):
                    with suppress(Exception):
                        dock.setProperty(_DOCK_DOCKED_AREA_PROP, int(cast("Any", area).value))
                        self._window.removeDockWidget(dock)

                logger.debug("collapse: %s is empty, flushing", _AREA_NAMES[area])
                self.remove_spacers_in_area(area)

                tmp = QDockWidget("", self._window)
                tmp.setProperty(_DOCK_SPACER_PROP, True)
                self._window.addDockWidget(area, tmp)
                self._window.removeDockWidget(tmp)
                tmp.deleteLater()
                need_layout_request = True

            if need_layout_request:
                QCoreApplication.sendEvent(self._window, QEvent(QEvent.Type.LayoutRequest))
        finally:
            self._window._collapsing_areas = False
            self._window._collapse_cooldown = True
            self._single_shot(1000, self._window._clear_collapse_cooldown)

    def create_spacer_dock(self, uid: str, area: Qt.DockWidgetArea) -> QDockWidget:
        spacer = QDockWidget("", None)
        spacer.setProperty(_DOCK_SPACER_PROP, True)
        spacer.setProperty(_DOCK_SPACER_FOR_PROP, uid)
        spacer.setObjectName(f"sr_spacer_{uid}")
        spacer.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        spacer.setAllowedAreas(area)
        spacer.setTitleBarWidget(QWidget())
        spacer.setWidget(QWidget())
        spacer.setMinimumSize(1, 1)
        spacer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._owner.log_dock(spacer, f"create_spacer area={self._owner.dock_area_name(area)}")
        return spacer


class MainWindowDockLayoutResetController:
    """Reset dock layout while preserving UILayoutSnapshot compatibility."""

    def __init__(
        self,
        window: Any,
        *,
        owner: Any,
        single_shot: Callable[[int, Callable[[], None]], None],
        load_initial_layout: Callable[[], Any],
    ) -> None:
        self._window = window
        self._owner = owner
        self._single_shot = single_shot
        self._load_initial_layout = load_initial_layout

    def reset_layout(self) -> None:
        """Reset window layout to the registered default dock arrangement."""
        logger.info("Layout reset requested")
        if self._window._reset_to_bundled_initial_layout():
            return
        self._window._reset_to_registered_default_layout()

    def reset_to_bundled_initial_layout(self) -> bool:
        snapshot = self._load_initial_layout()
        if snapshot is None:
            logger.warning("Bundled initial layout unavailable; falling back to dock defaults")
            return False

        layout_snapshot = UILayoutSnapshot(
            window_state=snapshot.window_state,
            window_geometry=snapshot.window_geometry,
            panels=snapshot.panels,
        )
        self._window.clear_transient_dock_spacers()
        self._window._apply_layout_snapshot(
            layout_snapshot,
            clear_missing=False,
            allow_restore_state=bool(layout_snapshot.window_state),
            force_panel_layout=True,
        )
        self._window._layout_restored = True
        self._single_shot(350, self._window._persist_reset_layout)
        return True

    def reset_to_registered_default_layout(self) -> None:
        self._window.clear_transient_dock_spacers()
        for pid, dock in self._window._panel_docks.items():
            if not is_valid(dock) or self._owner.is_spacer_dock(dock):
                continue
            default_area = self._owner.dock_area_from_default_property(dock)
            default_visible = dock.property(_DOCK_DEFAULT_VISIBLE_PROP)
            visible = bool(default_visible) if isinstance(default_visible, bool) else True
            with suppress(Exception):
                if dock.isFloating():
                    dock.setFloating(False)
                self._window.removeDockWidget(dock)
                self._window.addDockWidget(default_area, dock)
                dock.setProperty(_DOCK_DOCKED_AREA_PROP, int(cast("Any", default_area).value))
                dock.setVisible(visible)
            self._window._sync_action_from_dock(pid, visible)
        self._window.clear_transient_dock_spacers()
        self._window._collapse_empty_dock_areas()
        self._single_shot(0, self._window._collapse_empty_dock_areas)


class MainWindowDockLayoutController:
    """Coordinate dock sizing, spacer cleanup, and layout reset details."""

    _DOCK_SIZES_KEY = "ui/dock_sizes"

    def __init__(
        self,
        window: Any,
        *,
        single_shot: Callable[[int, Callable[[], None]], None],
        load_initial_layout: Callable[[], Any],
    ) -> None:
        self._window = window
        self._single_shot = single_shot
        self._load_initial_layout = load_initial_layout
        self._size_cache = MainWindowDockSizeCacheController(
            window,
            owner=self,
            single_shot=single_shot,
        )
        self._spacers = MainWindowDockSpacerController(
            window,
            owner=self,
            single_shot=single_shot,
        )
        self._layout_reset = MainWindowDockLayoutResetController(
            window,
            owner=self,
            single_shot=single_shot,
            load_initial_layout=load_initial_layout,
        )

    def initialize_state(self) -> None:
        """Initialize dock size cache and debug state on the MainWindow surface."""
        self._window._floating_size_cache = {}
        self._window._docked_size_cache = {}
        self._window._dock_sizes_dirty = False
        self._window._color_panel_min_width = None
        self.load_dock_sizes()
        self._window._dock_debug = self._window._settings.get_bool("ui/dock_debug", False)

    def dock_label(self, dock: QDockWidget) -> str:
        title = dock.windowTitle()
        name = dock.objectName()
        if title and name:
            return f"{title} [{name}]"
        if title:
            return title
        if name:
            return name
        return f"dock:{id(dock)}"

    def dock_area_name(self, area: Qt.DockWidgetArea) -> str:
        mapping = {
            Qt.DockWidgetArea.LeftDockWidgetArea: "left",
            Qt.DockWidgetArea.RightDockWidgetArea: "right",
            Qt.DockWidgetArea.TopDockWidgetArea: "top",
            Qt.DockWidgetArea.BottomDockWidgetArea: "bottom",
        }
        return mapping.get(area, "unknown")

    def log_dock(self, dock: QDockWidget, message: str) -> None:
        if not self._window._dock_debug:
            return
        try:
            area = self.dock_area_name(self._window.dockWidgetArea(dock))
        except Exception:
            logger.debug("_log_dock: suppressed exception", exc_info=True)
            area = "unknown"
        size = dock.size()
        logger.info(
            "DockDebug %s %s floating=%s area=%s size=%sx%s",
            self.dock_label(dock),
            message,
            dock.isFloating(),
            area,
            size.width(),
            size.height(),
        )

    def is_left_button(self, event: QEvent) -> bool:
        button = getattr(event, "button", None)
        return callable(button) and button() == Qt.MouseButton.LeftButton

    def handle_dock_filter_event(self, dock: QDockWidget, event: QEvent) -> None:
        event_type = event.type()
        handlers: dict[QEvent.Type, Callable[[], None]] = {
            QEvent.Type.NonClientAreaMouseButtonPress: lambda: self.handle_nonclient_press(
                dock, event
            ),
            QEvent.Type.NonClientAreaMouseButtonRelease: lambda: self.handle_nonclient_release(
                dock, event
            ),
            QEvent.Type.MouseButtonRelease: lambda: self.handle_button_release(dock, event),
            QEvent.Type.Move: lambda: self.handle_floating_move(dock),
            QEvent.Type.Resize: lambda: self.handle_floating_resize(dock),
        }
        handler = handlers.get(event_type)
        if handler is not None:
            handler()

    def handle_nonclient_press(self, dock: QDockWidget, event: QEvent) -> None:
        if not self.is_left_button(event):
            return
        if dock.isFloating():
            self.force_record_floating_size(dock)
            self.log_dock(dock, "drag_press_nonclient")
        else:
            self.snapshot_docked_size(dock)
            self.log_dock(dock, "drag_press_nonclient_docked")
        dock.setProperty(_DOCK_DRAGGING_PROP, True)

    def handle_nonclient_release(self, dock: QDockWidget, event: QEvent) -> None:
        if not self.is_left_button(event):
            return
        self.log_dock(dock, "drag_release_nonclient")
        self.schedule_drag_finalize(dock)

    def handle_button_release(self, dock: QDockWidget, event: QEvent) -> None:
        if not self.is_left_button(event):
            return
        self.log_dock(dock, "drag_release_client")
        self.schedule_drag_finalize(dock)

    def handle_floating_move(self, dock: QDockWidget) -> None:
        if not dock.isFloating():
            return
        if QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton:
            if not dock.property(_DOCK_DRAGGING_PROP):
                self.force_record_floating_size(dock)
                self.log_dock(dock, "drag_start_move")
            dock.setProperty(_DOCK_DRAGGING_PROP, True)
        elif dock.property(_DOCK_DRAGGING_PROP):
            self.schedule_drag_finalize(dock)

    def handle_floating_resize(self, dock: QDockWidget) -> None:
        if dock.isFloating():
            self.record_floating_size(dock)

    def dock_persist_key(self, dock: QDockWidget) -> str | None:
        """Return a stable key for persisting dock sizes, or None if unnamed."""
        name = dock.objectName()
        return name if name else None

    def load_dock_sizes(self) -> None:
        """Load saved dock sizes from settings into caches."""
        self._size_cache.load_dock_sizes(self._DOCK_SIZES_KEY)

    def save_dock_sizes(self) -> None:
        """Persist current dock sizes to settings."""
        self._size_cache.save_dock_sizes(self._DOCK_SIZES_KEY)

    def mark_dock_sizes_dirty(self) -> None:
        """Schedule a deferred save of dock sizes."""
        self._size_cache.mark_dock_sizes_dirty()

    def flush_dock_sizes(self) -> None:
        self._size_cache.flush_dock_sizes(self._DOCK_SIZES_KEY)

    def is_spacer_dock(self, dock: QDockWidget) -> bool:
        return self._spacers.is_spacer_dock(dock)

    def dock_uid(self, dock: QDockWidget) -> str:
        return self._spacers.dock_uid(dock)

    def floating_cache_key(self, dock: QDockWidget) -> str:
        return self._size_cache.floating_cache_key(dock)

    def last_floating_size(self, dock: QDockWidget) -> QSize | None:
        return self._size_cache.last_floating_size(dock)

    def record_floating_size(self, dock: QDockWidget) -> None:
        self._size_cache.record_floating_size(dock)

    def force_record_floating_size(self, dock: QDockWidget) -> None:
        self._size_cache.force_record_floating_size(dock)

    def snapshot_docked_size(self, dock: QDockWidget) -> None:
        """Save the dock's current size and area while it is docked."""
        self._size_cache.snapshot_docked_size(dock)

    def apply_docked_size(self, dock: QDockWidget) -> None:
        """Restore previously saved docked size via resizeDocks."""
        self._size_cache.apply_docked_size(dock)

    def schedule_drag_finalize(self, dock: QDockWidget) -> None:
        self._size_cache.schedule_drag_finalize(dock)

    def finalize_drag_release(self, dock: QDockWidget) -> None:
        self._size_cache.finalize_drag_release(dock)

    def start_floating_size_timer(self, dock: QDockWidget) -> None:
        self._size_cache.start_floating_size_timer(dock)

    def stop_floating_size_timer(self, dock: QDockWidget) -> None:
        self._size_cache.stop_floating_size_timer(dock)

    def apply_floating_size_retry(self, dock: QDockWidget, size: QSize) -> None:
        self._size_cache.apply_floating_size_retry(dock, size)

    def ensure_dock_tracking(self, dock: QDockWidget) -> None:
        if not is_valid(dock) or self.is_spacer_dock(dock):
            return
        install_floating_dock_guard(dock)
        if dock.property(_DOCK_TRACKING_PROP):
            return
        dock.setProperty(_DOCK_TRACKING_PROP, True)
        self.dock_uid(dock)
        if not dock.isFloating():
            area = self._window.dockWidgetArea(dock)
            if area != Qt.DockWidgetArea.NoDockWidgetArea:
                area_val = area.value if hasattr(area, "value") else int(cast("Any", area))
                dock.setProperty(_DOCK_DOCKED_AREA_PROP, area_val)
        self.log_dock(dock, "tracking_enabled")
        key = self.dock_persist_key(dock)
        if key:
            cached_float = self._window._floating_size_cache.get(f"name:{key}")
            if isinstance(cached_float, QSize):
                dock.setProperty(_DOCK_FLOAT_SIZE_PROP, cached_float)
                dock.setProperty(_DOCK_PREDOCK_SIZE_PROP, cached_float)
            cached_docked = self._window._docked_size_cache.get(key)
            if isinstance(cached_docked, list) and len(cached_docked) == 2:
                dock.setProperty(_DOCK_DOCKED_SIZE_PROP, QSize(cached_docked[0], cached_docked[1]))
        dock.topLevelChanged.connect(
            lambda floating, d=dock: self._dispatch_dock_top_level_changed(d, floating)
        )
        dock.visibilityChanged.connect(
            lambda visible, d=dock: self._dispatch_dock_visibility_changed(d, visible)
        )
        dock.installEventFilter(self._window)
        dock.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        dock.customContextMenuRequested.connect(
            lambda pos, d=dock: self._dispatch_dock_context_menu_requested(d, pos)
        )
        if dock.isFloating():
            self.start_floating_size_timer(dock)
            self.record_floating_size(dock)

    def _dispatch_dock_top_level_changed(self, dock: QDockWidget, floating: bool) -> None:
        if not is_valid(self._window) or not is_valid(dock):
            return
        self._window._on_dock_top_level_changed(dock, floating)

    def _dispatch_dock_visibility_changed(self, dock: QDockWidget, visible: bool) -> None:
        if not is_valid(self._window) or not is_valid(dock):
            return
        self._window._on_dock_visibility_changed(dock, visible)

    def _dispatch_dock_context_menu_requested(self, dock: QDockWidget, pos: Any) -> None:
        if not is_valid(self._window) or not is_valid(dock):
            return
        self._window._show_dock_context_menu(dock, pos)

    def show_dock_context_menu(self, dock: QDockWidget, pos: Any) -> None:
        """Show context menu for stuck dock recovery."""
        if not is_valid(dock):
            return
        menu = QMenu(self._window)
        if dock.isFloating():
            act = menu.addAction(self._window.tr("Dock"))
            act.triggered.connect(lambda: self._window._force_dock(dock))
        else:
            act = menu.addAction(self._window.tr("Float"))
            act.triggered.connect(lambda: self._window._force_float(dock))
        menu.exec(dock.mapToGlobal(pos))

    def force_float(self, dock: QDockWidget) -> None:
        """Force a dock widget to floating state (recovery action)."""
        if not is_valid(dock) or dock.isFloating():
            return
        self.apply_panel_min_size(dock)
        dock.setMaximumSize(_MAX_QWIDGET_SIZE, _MAX_QWIDGET_SIZE)
        dock.setFloating(True)
        self.log_dock(dock, "force_float (context menu)")

    def force_dock(self, dock: QDockWidget) -> None:
        """Force a floating dock widget back to docked state (recovery action)."""
        if not is_valid(dock) or not dock.isFloating():
            return
        dock.setFloating(False)
        self.log_dock(dock, "force_dock (context menu)")

    def on_dock_top_level_changed(self, dock: QDockWidget, floating: bool) -> None:
        if not is_valid(dock) or self.is_spacer_dock(dock):
            return
        self.log_dock(dock, f"topLevelChanged floating={floating}")
        if floating:
            self.snapshot_docked_size(dock)
            self.remove_spacer_for_uid(self.dock_uid(dock))
            size = dock.property(_DOCK_PREDOCK_SIZE_PROP)
            if not isinstance(size, QSize):
                size = self.last_floating_size(dock)
            if size is not None:
                self.apply_floating_size_retry(dock, size)
            self.start_floating_size_timer(dock)
            if not getattr(self._window, "_collapsing_areas", False):
                self.schedule_collapse_empty_dock_areas()
            return
        saved = self.last_floating_size(dock)
        if saved is not None:
            dock.setProperty(_DOCK_PREDOCK_SIZE_PROP, saved)
            self.log_dock(dock, f"predock_snapshot {saved.width()}x{saved.height()}")
        dock.setProperty(_DOCK_DRAGGING_PROP, False)
        dock.setProperty(_DOCK_DRAGGING_RELEASE_PROP, False)
        self.stop_floating_size_timer(dock)
        for ms in (0, 80, 200):

            def apply_docked_size(d: QDockWidget = dock) -> None:
                self._window._apply_docked_size(d)

            self._single_shot(ms, apply_docked_size)

        def apply_empty_area_sizing(d: QDockWidget = dock) -> None:
            self._window._apply_empty_area_sizing(d)

        self._single_shot(0, apply_empty_area_sizing)

    def on_dock_visibility_changed(self, dock: QDockWidget, visible: bool) -> None:
        if not is_valid(dock) or self.is_spacer_dock(dock):
            return
        self.log_dock(dock, f"visibilityChanged visible={visible}")
        if not visible:
            self.stop_floating_size_timer(dock)
            self.remove_spacer_for_uid(self.dock_uid(dock))
            if not getattr(self._window, "_collapsing_areas", False):
                self.schedule_collapse_empty_dock_areas()
            return
        if dock.isFloating():
            return

        def apply_empty_area_sizing(d: QDockWidget = dock) -> None:
            self._window._apply_empty_area_sizing(d)

        self._single_shot(0, apply_empty_area_sizing)

    def apply_floating_size(self, dock: QDockWidget, size: QSize) -> None:
        self._size_cache.apply_floating_size(dock, size)

    def apply_empty_area_sizing(self, dock: QDockWidget) -> None:
        self._spacers.apply_empty_area_sizing(dock)

    def visible_real_docks_in_area(
        self,
        area: Qt.DockWidgetArea,
        *,
        exclude: QDockWidget | None = None,
    ) -> list[QDockWidget]:
        return self._spacers.visible_real_docks_in_area(area, exclude=exclude)

    def find_spacer_for_uid(self, uid: str) -> QDockWidget | None:
        return self._spacers.find_spacer_for_uid(uid)

    def remove_spacer_for_uid(self, uid: str) -> None:
        self._spacers.remove_spacer_for_uid(uid)

    def remove_spacers_in_area(self, area: Qt.DockWidgetArea) -> None:
        self._spacers.remove_spacers_in_area(area)

    def remove_spacer_dock(self, dock: QDockWidget, *, reason: str) -> None:
        self._spacers.remove_spacer_dock(dock, reason=reason)

    def spacer_docks(self) -> list[QDockWidget]:
        return self._spacers.spacer_docks()

    def real_dock_for_uid(self, uid: str) -> QDockWidget | None:
        return self._spacers.real_dock_for_uid(uid)

    def prune_stale_spacer_docks(self) -> bool:
        """Remove spacer docks whose owning real dock no longer occupies the area."""
        return self._spacers.prune_stale_spacer_docks()

    def clear_transient_dock_spacers(self) -> bool:
        """Drop internal spacer docks before persisting or exporting layouts."""
        return self._spacers.clear_transient_dock_spacers()

    def hidden_real_docks_in_area(self, area: Qt.DockWidgetArea) -> list[QDockWidget]:
        return self._spacers.hidden_real_docks_in_area(area)

    def dock_area_from_saved_property(self, dock: QDockWidget) -> Qt.DockWidgetArea:
        return self._spacers.dock_area_from_saved_property(dock)

    def dock_area_from_default_property(self, dock: QDockWidget) -> Qt.DockWidgetArea:
        return self._spacers.dock_area_from_default_property(dock)

    def ensure_dock_attached_for_show(self, dock: QDockWidget) -> None:
        self._spacers.ensure_dock_attached_for_show(dock)

    def enable_dock_collapse(self) -> None:
        self._spacers.enable_dock_collapse()

    def schedule_collapse_empty_dock_areas(self) -> None:
        """Schedule a deferred collapse check, coalescing multiple requests."""
        self._spacers.schedule_collapse_empty_dock_areas()

    def clear_collapse_cooldown(self) -> None:
        self._spacers.clear_collapse_cooldown()

    def collapse_empty_dock_areas(self) -> None:
        """Redistribute space in dock areas after docks are floated."""
        self._spacers.collapse_empty_dock_areas()

    def create_spacer_dock(self, uid: str, area: Qt.DockWidgetArea) -> QDockWidget:
        return self._spacers.create_spacer_dock(uid, area)

    def apply_panel_min_size(self, dock: QDockWidget | None) -> None:
        """Apply the common minimum size for docked/floating panels."""
        if dock is None or self.is_spacer_dock(dock):
            return
        dock.setMinimumSize(_PANEL_MIN_SIZE)
        content = dock.widget()
        if content is not None:
            content.setMinimumSize(0, 0)

    def apply_color_panel_min_width(
        self,
        dock: QDockWidget | None,
        *,
        fallback: int | None = None,
    ) -> None:
        del fallback
        self.apply_panel_min_size(dock)

    def sync_color_panel_min_width(self) -> None:
        for panel_id in (
            "ui.layers",
            "ui.nano_banana",
            "ui.gptimage2",
            "ui.comfyui",
            "ui.text_font",
        ):
            dock = self._window.get_panel_dock(panel_id)
            self.apply_panel_min_size(dock)

    def reset_layout(self) -> None:
        """Reset window layout to the registered default dock arrangement."""
        self._layout_reset.reset_layout()

    def reset_to_bundled_initial_layout(self) -> bool:
        return self._layout_reset.reset_to_bundled_initial_layout()

    def reset_to_registered_default_layout(self) -> None:
        self._layout_reset.reset_to_registered_default_layout()
