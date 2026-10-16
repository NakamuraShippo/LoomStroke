"""Qt-based implementation of the UILayoutService protocol."""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any, Protocol

from PySide6.QtCore import QRect, Qt, QThread, QTimer
from PySide6.QtWidgets import QDockWidget, QMainWindow, QToolBar

from ..host_api import PanelLayout, UILayoutSnapshot
from .panel_ids import canonical_panel_id, panel_lookup_ids


class UILayoutServiceProtocol(Protocol):
    def snapshot(self, *, include_panels: bool = ...) -> UILayoutSnapshot: ...

    def apply(
        self,
        snapshot: UILayoutSnapshot,
        *,
        allow_restore_state: bool = ...,
        clear_missing: bool = ...,
        preserve_toolbars: bool = ...,
        force_panel_layout: bool = ...,
    ) -> None: ...

    def iter_panels(self) -> Iterable[PanelLayout]: ...


logger = logging.getLogger(__name__)

_RESTORE_AREA_AFTER_STATE_PANEL_IDS = frozenset({"ui.ai_effect_manager"})
_LAYOUT_TAB_GROUP_KEY = "layout_tab_group"
_LAYOUT_TAB_ORDER_KEY = "layout_tab_order"


class QtUILayoutService(UILayoutServiceProtocol):
    """Capture and restore QMainWindow dock layouts."""

    def __init__(
        self,
        window: QMainWindow,
        panel_provider: Callable[[], Iterable[tuple[str, QDockWidget]]] | None = None,
        *,
        settings_guard: Callable[[bool], None] | None = None,
    ) -> None:
        self._window = window
        self._settings_guard = settings_guard
        self._panel_provider = panel_provider or self._default_panel_provider
        self._logger = logging.getLogger(self.__class__.__name__)
        self._pending_layout_tab_panels: tuple[PanelLayout, ...] = ()

    # ------------------------------------------------------------------
    def snapshot(self, *, include_panels: bool = True) -> UILayoutSnapshot:
        if not self._on_main_thread():
            self._logger.warning("snapshot called outside Qt main thread")
        clear_spacers = getattr(self._window, "clear_transient_dock_spacers", None)
        if callable(clear_spacers):
            clear_spacers()
        window_state = self._encode_bytes(self._window.saveState())
        window_geometry = self._encode_bytes(self._window.saveGeometry())
        panels: Sequence[PanelLayout] = ()
        if include_panels:
            panels = tuple(self._collect_panel_layouts())
        return UILayoutSnapshot(
            window_state=window_state,
            window_geometry=window_geometry,
            panels=panels,
        )

    def apply(
        self,
        snapshot: UILayoutSnapshot,
        *,
        allow_restore_state: bool = True,
        clear_missing: bool = False,
        preserve_toolbars: bool = False,
        force_panel_layout: bool = False,
    ) -> None:
        if not self._on_main_thread():

            def _deferred_apply() -> None:
                self.apply(
                    snapshot,
                    allow_restore_state=allow_restore_state,
                    clear_missing=clear_missing,
                    preserve_toolbars=preserve_toolbars,
                    force_panel_layout=force_panel_layout,
                )

            QTimer.singleShot(0, self._window, _deferred_apply)
            return

        guard = self._settings_guard or (lambda _flag: None)
        toolbar_visibility = self._collect_toolbar_visibility() if preserve_toolbars else ()
        guard(True)
        state_restored = False
        panels = tuple(snapshot.panels)
        try:
            if allow_restore_state and snapshot.window_state:
                state_restored = self._restore_window_state(snapshot.window_state)
            geometry_payload, geometry_restored = self._restore_initial_window_geometry(
                snapshot.window_geometry
            )

            panel_map = self._build_panel_lookup_map()
            for layout in self._ordered_panel_layouts(
                panels,
                state_restored=state_restored,
            ):
                dock = panel_map.get(layout.panel_id)
                if dock is None:
                    self._logger.debug("Panel %s missing during layout apply", layout.panel_id)
                    continue
                self._apply_panel_layout(
                    dock,
                    layout,
                    state_restored=state_restored,
                    force_panel_layout=force_panel_layout,
                )
            self._apply_layout_group_splits_if_needed(
                panels,
                panel_map,
                force_panel_layout=force_panel_layout,
                state_restored=state_restored,
            )
            if clear_missing:
                known = self._known_panel_ids(panels)
                for panel_id, dock in self._panel_provider():
                    if canonical_panel_id(panel_id) not in known:
                        dock.setVisible(False)
                        action = dock.toggleViewAction()
                        if action:
                            action.setChecked(False)
            if force_panel_layout:
                self._detach_non_layout_tab_panels(panels, panel_map)
            self._apply_layout_tab_groups(panels, panel_map)
            self._schedule_layout_tab_group_apply(panels)
            self._finish_window_geometry_restore(geometry_payload, geometry_restored)
        finally:
            if toolbar_visibility:
                self._restore_toolbar_visibility(toolbar_visibility)
            guard(False)

    def iter_panels(self) -> Iterable[PanelLayout]:
        """Iterate over known panels as layout records."""
        return tuple(self._collect_panel_layouts())

    def reapply_layout_tab_groups(self) -> None:
        """Re-run tab grouping for panels that arrived after layout restore."""

        if not self._pending_layout_tab_panels:
            return
        if not self._on_main_thread():

            def _deferred_apply() -> None:
                self.reapply_layout_tab_groups()

            QTimer.singleShot(0, self._window, _deferred_apply)
            return
        panel_map = self._build_panel_lookup_map()
        self._apply_layout_tab_groups(self._pending_layout_tab_panels, panel_map)

    # ------------------------------------------------------------------
    def _build_panel_lookup_map(self) -> dict[str, QDockWidget]:
        panel_map: dict[str, QDockWidget] = {}
        for panel_id, dock in self._panel_provider():
            for lookup_id in panel_lookup_ids(panel_id):
                panel_map.setdefault(lookup_id, dock)
        return panel_map

    def _known_panel_ids(self, panels: Iterable[PanelLayout]) -> set[str]:
        return {canonical_panel_id(layout.panel_id) for layout in panels}

    def _collect_panel_layouts(self) -> Iterator[PanelLayout]:
        entries = tuple(self._panel_provider())
        tab_metadata = self._collect_tab_group_metadata(entries)
        for z_index, (panel_id, dock) in enumerate(entries):
            rect = dock.geometry()
            geometry = (rect.x(), rect.y(), rect.width(), rect.height())
            dock_area = self._dock_area_name(self._window.dockWidgetArea(dock))
            yield PanelLayout(
                panel_id=panel_id,
                geometry=geometry,
                dock_area=dock_area,
                floating=dock.isFloating(),
                visible=dock.isVisible(),
                z_index=z_index,
                metadata=tab_metadata.get(id(dock)),
            )

    def _collect_tab_group_metadata(
        self,
        entries: Iterable[tuple[str, QDockWidget]],
    ) -> dict[int, dict[str, object]]:
        ordered_entries = tuple(entries)
        dock_order = {id(dock): index for index, (_panel_id, dock) in enumerate(ordered_entries)}
        known_dock_ids = set(dock_order)
        metadata: dict[int, dict[str, object]] = {}
        seen_groups: set[frozenset[int]] = set()

        for _panel_id, dock in ordered_entries:
            group_ids: list[int] = []
            for group_dock in (dock, *self._window.tabifiedDockWidgets(dock)):
                group_dock_id = id(group_dock)
                if group_dock_id in known_dock_ids and group_dock_id not in group_ids:
                    group_ids.append(group_dock_id)
            if len(group_ids) < 2:
                continue

            group_key = frozenset(group_ids)
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            group_name = f"tab_group_{len(seen_groups) - 1}"
            for order, group_dock_id in enumerate(
                sorted(group_key, key=lambda dock_id: dock_order[dock_id])
            ):
                metadata[group_dock_id] = {
                    _LAYOUT_TAB_GROUP_KEY: group_name,
                    _LAYOUT_TAB_ORDER_KEY: order,
                }
        return metadata

    def _apply_panel_layout(
        self,
        dock: QDockWidget,
        layout: PanelLayout,
        *,
        state_restored: bool = False,
        force_panel_layout: bool = False,
    ) -> None:
        if layout.floating:
            dock.setFloating(True)
            dock.setGeometry(QRect(*layout.geometry))
        elif not state_restored:
            # Avoid addDockWidget() after restoreState() for normal panels:
            # Qt state already positions docks including tabified groups.
            self._apply_docked_panel_layout(dock, layout)
        elif layout.panel_id in _RESTORE_AREA_AFTER_STATE_PANEL_IDS:
            # Late-registered panels listed above may not be represented
            # correctly in the Qt state, so their JSON area is a fallback.
            # If restoreState() already placed the dock in the saved area,
            # keep that richer Qt state intact so tabified groups survive.
            self._apply_late_panel_area_fallback(dock, layout)
        elif force_panel_layout:
            # Bundled initial layouts still need JSON areas as a fallback, but
            # re-adding every dock after restoreState() splits Qt-restored tabs.
            self._apply_late_panel_area_fallback(dock, layout)
        elif self._has_layout_tab_group(layout):
            # Qt restoreState() can succeed even when a dock was not represented
            # in that state.  Metadata tab groups need all members in the JSON
            # dock area before tabifyDockWidget() is retried.
            self._apply_late_panel_area_fallback(dock, layout)
        dock.setVisible(layout.visible)
        action = dock.toggleViewAction()
        if action:
            action.setChecked(layout.visible)

    def _ordered_panel_layouts(
        self,
        panels: Iterable[PanelLayout],
        *,
        state_restored: bool,
    ) -> tuple[PanelLayout, ...]:
        panel_tuple = tuple(panels)
        if state_restored:
            return tuple(sorted(panel_tuple, key=lambda item: item.z_index))
        group_keys = self._layout_group_sort_keys(panel_tuple)
        return tuple(
            sorted(
                panel_tuple,
                key=lambda panel: (
                    group_keys.get(self._layout_group_identity(panel), self._panel_sort_key(panel)),
                    self._layout_panel_order(panel),
                    panel.z_index,
                ),
            )
        )

    def _layout_group_sort_keys(
        self,
        panels: Iterable[PanelLayout],
    ) -> dict[tuple[str, str], tuple[int, int, int, int]]:
        groups: dict[tuple[str, str], list[PanelLayout]] = {}
        for panel in panels:
            groups.setdefault(self._layout_group_identity(panel), []).append(panel)

        return {
            group_id: self._panel_sort_key(self._layout_group_anchor(group_panels))
            for group_id, group_panels in groups.items()
        }

    def _layout_group_identity(self, panel: PanelLayout) -> tuple[str, str]:
        metadata = panel.metadata or {}
        group = metadata.get(_LAYOUT_TAB_GROUP_KEY)
        if isinstance(group, str) and group.strip():
            return ("group", group.strip())
        return ("panel", canonical_panel_id(panel.panel_id))

    def _layout_group_anchor(self, panels: Iterable[PanelLayout]) -> PanelLayout:
        panel_tuple = tuple(panels)
        for panel in sorted(panel_tuple, key=lambda item: item.z_index):
            x, y, width, height = panel.geometry
            if x >= 0 and y >= 0 and width > 0 and height > 0:
                return panel
        return min(panel_tuple, key=lambda item: item.z_index)

    def _panel_sort_key(self, panel: PanelLayout) -> tuple[int, int, int, int]:
        x, y, _width, _height = panel.geometry
        area_rank = self._dock_area_sort_rank(panel.dock_area)
        if panel.dock_area in {"left", "right"}:
            return (area_rank, y, x, panel.z_index)
        if panel.dock_area in {"top", "bottom"}:
            return (area_rank, x, y, panel.z_index)
        return (area_rank, panel.z_index, y, x)

    def _dock_area_sort_rank(self, dock_area: str | None) -> int:
        if dock_area == "left":
            return 0
        if dock_area == "right":
            return 1
        if dock_area == "top":
            return 2
        if dock_area == "bottom":
            return 3
        return 4

    def _layout_panel_order(self, panel: PanelLayout) -> int:
        metadata = panel.metadata or {}
        return self._coerce_tab_order(metadata.get(_LAYOUT_TAB_ORDER_KEY), panel.z_index)

    def _apply_layout_group_splits_if_needed(
        self,
        panels: Iterable[PanelLayout],
        panel_map: dict[str, QDockWidget],
        *,
        force_panel_layout: bool,
        state_restored: bool,
    ) -> None:
        if force_panel_layout and not state_restored:
            self._apply_layout_group_splits(panels, panel_map)

    def _apply_layout_group_splits(
        self,
        panels: Iterable[PanelLayout],
        panel_map: dict[str, QDockWidget],
    ) -> None:
        """Recreate stacked dock groups when Qt state is unavailable."""

        area_groups: dict[str, list[tuple[PanelLayout, PanelLayout, QDockWidget]]] = {}
        grouped: dict[tuple[str, str], list[PanelLayout]] = {}
        for panel in panels:
            if panel.floating or not panel.dock_area:
                continue
            grouped.setdefault(self._layout_group_identity(panel), []).append(panel)

        for group_panels in grouped.values():
            anchor_layout = self._layout_group_anchor(group_panels)
            base_layout = self._layout_group_base(group_panels)
            dock = panel_map.get(canonical_panel_id(base_layout.panel_id))
            if dock is None:
                continue
            area_groups.setdefault(anchor_layout.dock_area or "", []).append(
                (anchor_layout, base_layout, dock)
            )

        for dock_area, groups in area_groups.items():
            if len(groups) < 2:
                continue
            area = self._dock_area_from_name(dock_area)
            orientation = self._split_orientation_for_area(dock_area)
            if area is None or orientation is None:
                continue

            previous: QDockWidget | None = None
            for anchor_layout, _base_layout, dock in sorted(
                groups,
                key=lambda item: self._panel_sort_key(item[0]),
            ):
                self._attach_split_base_dock(dock, area, previous, orientation)
                dock.resize(anchor_layout.geometry[2], anchor_layout.geometry[3])
                previous = dock

    def _attach_split_base_dock(
        self,
        dock: QDockWidget,
        area: Qt.DockWidgetArea,
        previous: QDockWidget | None,
        orientation: Qt.Orientation,
    ) -> None:
        if dock.isFloating():
            dock.setFloating(False)
        if dock.parent() is not self._window:
            dock.setParent(self._window)
        if previous is None:
            self._window.addDockWidget(area, dock)
            return
        try:
            self._window.splitDockWidget(previous, dock, orientation)
        except RuntimeError:
            self._logger.warning(
                "Failed to split dock group base %s near %s",
                dock.objectName(),
                previous.objectName(),
                exc_info=True,
            )

    def _layout_group_base(self, panels: Iterable[PanelLayout]) -> PanelLayout:
        return min(
            tuple(panels),
            key=lambda panel: (self._layout_panel_order(panel), panel.z_index),
        )

    def _split_orientation_for_area(self, dock_area: str | None) -> Qt.Orientation | None:
        if dock_area in {"left", "right"}:
            return Qt.Orientation.Vertical
        if dock_area in {"top", "bottom"}:
            return Qt.Orientation.Horizontal
        return None

    def _apply_docked_panel_layout(self, dock: QDockWidget, layout: PanelLayout) -> None:
        area = self._dock_area_from_name(layout.dock_area)
        if area is not None:
            if dock.isFloating():
                dock.setFloating(False)
            self._window.addDockWidget(area, dock)
        dock.resize(layout.geometry[2], layout.geometry[3])

    def _apply_late_panel_area_fallback(
        self,
        dock: QDockWidget,
        layout: PanelLayout,
        *,
        force_reattach: bool = False,
    ) -> None:
        area = self._dock_area_from_name(layout.dock_area)
        if area is None:
            return
        if force_reattach:
            if dock.isFloating():
                dock.setFloating(False)
            if dock.parent() is not self._window:
                dock.setParent(self._window)
            self._window.removeDockWidget(dock)
            self._window.addDockWidget(area, dock)
            if dock.isFloating():
                dock.setFloating(False)
            dock.resize(layout.geometry[2], layout.geometry[3])
            return
        was_floating = dock.isFloating()
        was_detached = dock.window() is not self._window
        current_area = self._window.dockWidgetArea(dock)
        if was_floating or was_detached:
            dock.setFloating(False)
        if was_floating or was_detached or current_area == Qt.DockWidgetArea.NoDockWidgetArea:
            self._window.addDockWidget(area, dock)
            dock.resize(layout.geometry[2], layout.geometry[3])
            return
        if current_area == area:
            return
        self._window.addDockWidget(area, dock)
        dock.resize(layout.geometry[2], layout.geometry[3])

    def _detach_non_layout_tab_panels(
        self,
        panels: Iterable[PanelLayout],
        panel_map: dict[str, QDockWidget],
    ) -> None:
        grouped_dock_ids: set[int] = set()
        ungrouped: list[tuple[QDockWidget, PanelLayout]] = []
        for layout in panels:
            dock = panel_map.get(canonical_panel_id(layout.panel_id))
            if dock is None or layout.floating:
                continue
            if self._has_layout_tab_group(layout):
                grouped_dock_ids.add(id(dock))
            else:
                ungrouped.append((dock, layout))

        if not grouped_dock_ids:
            return
        for dock, layout in ungrouped:
            tabified_ids = {id(tab) for tab in self._window.tabifiedDockWidgets(dock)}
            if tabified_ids & grouped_dock_ids:
                self._apply_late_panel_area_fallback(dock, layout, force_reattach=True)

    def _apply_layout_tab_groups(
        self,
        panels: Iterable[PanelLayout],
        panel_map: dict[str, QDockWidget],
    ) -> None:
        groups: dict[str, list[tuple[int, QDockWidget]]] = {}
        for layout in panels:
            metadata = layout.metadata or {}
            group = metadata.get(_LAYOUT_TAB_GROUP_KEY)
            if not isinstance(group, str) or not group.strip():
                continue
            dock = panel_map.get(canonical_panel_id(layout.panel_id))
            if dock is None or layout.floating:
                continue
            self._apply_late_panel_area_fallback(dock, layout)
            if dock.isFloating():
                continue
            order = self._coerce_tab_order(metadata.get(_LAYOUT_TAB_ORDER_KEY), layout.z_index)
            groups.setdefault(group.strip(), []).append((order, dock))

        for group_name, entries in groups.items():
            docks: list[QDockWidget] = []
            seen: set[int] = set()
            for _order, dock in sorted(entries, key=lambda item: item[0]):
                dock_id = id(dock)
                if dock_id in seen:
                    continue
                docks.append(dock)
                seen.add(dock_id)
            if len(docks) < 2:
                continue

            base = docks[0]
            for dock in docks[1:]:
                try:
                    self._window.tabifyDockWidget(base, dock)
                except RuntimeError:
                    self._logger.warning(
                        "Failed to tabify panel %s into layout tab group %s",
                        dock.objectName(),
                        group_name,
                        exc_info=True,
                    )
            try:
                base.raise_()
            except RuntimeError:
                self._logger.debug(
                    "Failed to raise layout tab group base %s",
                    base.objectName(),
                    exc_info=True,
                )

    def _schedule_layout_tab_group_apply(self, panels: Iterable[PanelLayout]) -> None:
        panel_tuple = tuple(panels)
        if not any(self._has_layout_tab_group(panel) for panel in panel_tuple):
            self._pending_layout_tab_panels = ()
            return
        self._pending_layout_tab_panels = panel_tuple

        def _deferred_apply() -> None:
            self.reapply_layout_tab_groups()

        QTimer.singleShot(0, self._window, _deferred_apply)
        QTimer.singleShot(250, self._window, _deferred_apply)

    def _has_layout_tab_group(self, panel: PanelLayout) -> bool:
        metadata = panel.metadata or {}
        group = metadata.get(_LAYOUT_TAB_GROUP_KEY)
        return isinstance(group, str) and bool(group.strip())

    def _coerce_tab_order(self, value: Any, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(fallback)

    def _default_panel_provider(self) -> Iterable[tuple[str, QDockWidget]]:
        panels: list[tuple[str, QDockWidget]] = []
        for index, dock in enumerate(self._window.findChildren(QDockWidget)):
            panel_id = canonical_panel_id(dock.objectName() or f"dock-{index}")
            panels.append((panel_id, dock))
        return panels

    def _collect_toolbar_visibility(self) -> tuple[tuple[QToolBar, bool], ...]:
        return tuple(
            (toolbar, not toolbar.isHidden()) for toolbar in self._window.findChildren(QToolBar)
        )

    def _restore_toolbar_visibility(
        self,
        toolbar_visibility: tuple[tuple[QToolBar, bool], ...],
    ) -> None:
        for toolbar, visible in toolbar_visibility:
            toolbar.setVisible(visible)
            action = toolbar.toggleViewAction()
            if action:
                action.setChecked(visible)

    def _restore_window_state(self, payload: str) -> bool:
        try:
            restored = self._window.restoreState(self._decode_bytes(payload))
        except Exception:
            self._logger.warning(
                "Failed to restore window state; applying panel fallback",
                exc_info=True,
            )
            return False
        if not restored:
            self._logger.warning(
                "restoreState returned false; applying panel fallback",
            )
        return bool(restored)

    def _restore_initial_window_geometry(self, payload: str | None) -> tuple[str | None, bool]:
        if not payload:
            return None, False
        return payload, self._restore_window_geometry(payload)

    def _finish_window_geometry_restore(self, payload: str | None, restored: bool) -> None:
        if not payload or not restored:
            return
        self._restore_window_geometry(payload)
        self._schedule_window_geometry_restore(payload)

    def _restore_window_geometry(self, payload: str) -> bool:
        try:
            restored = self._window.restoreGeometry(self._decode_bytes(payload))
        except Exception:
            self._logger.warning(
                "Failed to restore window geometry; continuing with current geometry",
                exc_info=True,
            )
            return False
        if not restored:
            self._logger.warning(
                "restoreGeometry returned false; continuing with current geometry",
            )
        return bool(restored)

    def _schedule_window_geometry_restore(self, payload: str) -> None:
        def _deferred_restore() -> None:
            self._restore_window_geometry(payload)

        QTimer.singleShot(0, self._window, _deferred_restore)
        QTimer.singleShot(250, self._window, _deferred_restore)

    def _encode_bytes(self, data: Any) -> str | None:
        if not data:
            return None
        raw = data if isinstance(data, bytes) else bytes(data)
        return base64.b64encode(raw).decode("ascii")

    def _decode_bytes(self, payload: str) -> bytes:
        return base64.b64decode(payload.encode("ascii"), validate=True)

    def _dock_area_name(self, area: Qt.DockWidgetArea) -> str | None:
        mapping = {
            Qt.DockWidgetArea.LeftDockWidgetArea: "left",
            Qt.DockWidgetArea.RightDockWidgetArea: "right",
            Qt.DockWidgetArea.TopDockWidgetArea: "top",
            Qt.DockWidgetArea.BottomDockWidgetArea: "bottom",
        }
        return mapping.get(area)

    def _dock_area_from_name(self, name: str | None) -> Qt.DockWidgetArea | None:
        if name is None:
            return None
        reverse = {
            "left": Qt.DockWidgetArea.LeftDockWidgetArea,
            "right": Qt.DockWidgetArea.RightDockWidgetArea,
            "top": Qt.DockWidgetArea.TopDockWidgetArea,
            "bottom": Qt.DockWidgetArea.BottomDockWidgetArea,
        }
        return reverse.get(name.lower())

    def _on_main_thread(self) -> bool:
        return QThread.currentThread() is self._window.thread()


__all__ = ["QtUILayoutService"]
