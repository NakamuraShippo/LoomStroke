"""Canvas context menu coordination for MainWindow."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

logger = logging.getLogger(__name__)

ActionSlot = Callable[..., Any]


@dataclass(slots=True)
class CanvasContextEntry:
    action_id: str | None
    label: str | None
    handler: ActionSlot | None
    shortcut: str | None
    shortcut_category: str
    shortcut_description: str | None
    group: str
    order: int
    enabled: Callable[[], bool] | None = None
    action: QAction | None = None


class MainWindowCanvasContextMenuCoordinator:
    """Register and materialize canvas context menu entries."""

    def __init__(
        self,
        window: Any,
        *,
        menu_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._window = window
        self._menu_factory = menu_factory or (lambda parent: QMenu(parent))

    def register_action(
        self,
        action_id: str | None,
        *,
        label: str | None = None,
        handler: ActionSlot | None = None,
        shortcut: str | None = None,
        shortcut_category: str = "Canvas",
        shortcut_description: str | None = None,
        group: str = "custom",
        order: int = 100,
        enabled: Callable[[], bool] | None = None,
    ) -> None:
        window = self._window
        key = action_id or (label or "")
        if not key:
            logger.warning("Canvas context action missing id/label")
            return
        if action_id and label and window._shortcut_manager:
            window._register_shortcut_definition(
                action_id,
                label=label,
                default=shortcut,
                category=shortcut_category,
                description=shortcut_description,
            )
        window._canvas_context_entries[key] = CanvasContextEntry(
            action_id=action_id,
            label=label,
            handler=handler,
            shortcut=shortcut,
            shortcut_category=shortcut_category,
            shortcut_description=shortcut_description,
            group=group,
            order=order,
            enabled=enabled,
        )
        if (
            action_id
            and label
            and handler
            and action_id not in window._shortcut_actions
            and window._shortcut_manager
        ):
            window._canvas_context_entries[key].action = window._mk_action(
                label,
                handler,
                shortcut,
                action_id=action_id,
                shortcut_category=shortcut_category,
                shortcut_description=shortcut_description,
            )

    def unregister_action(self, key: str) -> None:
        self._window._canvas_context_entries.pop(key, None)

    def register_default_actions(self) -> None:
        window = self._window
        defaults: list[dict[str, Any]] = [
            {
                "action_id": "edit.undo",
                "label": "&Undo",
                "handler": window._on_undo,
                "shortcut": "Ctrl+Z",
                "shortcut_category": "Edit",
                "group": "edit",
                "order": 10,
            },
            {
                "action_id": "edit.redo",
                "label": "&Redo",
                "handler": window._on_redo,
                "shortcut": "Ctrl+Y",
                "shortcut_category": "Edit",
                "group": "edit",
                "order": 20,
            },
            {
                "action_id": "edit.cut",
                "label": "Cu&t",
                "handler": window._on_cut,
                "shortcut": "Ctrl+X",
                "shortcut_category": "Edit",
                "group": "edit",
                "order": 30,
            },
            {
                "action_id": "edit.copy",
                "label": "&Copy",
                "handler": window._on_copy,
                "shortcut": "Ctrl+C",
                "shortcut_category": "Edit",
                "group": "edit",
                "order": 40,
            },
            {
                "action_id": "edit.paste",
                "label": "&Paste",
                "handler": window._on_paste,
                "shortcut": "Ctrl+V",
                "shortcut_category": "Edit",
                "group": "edit",
                "order": 50,
            },
            {
                "action_id": "edit.select_all",
                "label": "Select &All",
                "handler": window._on_select_all,
                "shortcut": "Ctrl+A",
                "shortcut_category": "Edit",
                "group": "edit",
                "order": 60,
            },
            {
                "action_id": "edit.select_all_vectors",
                "label": "Select &All Vectors",
                "handler": window._on_select_all_vectors,
                "shortcut": "Ctrl+L",
                "shortcut_category": "Edit",
                "group": "edit",
                "order": 65,
            },
            {
                "action_id": "shape_manager.register_shape",
                "label": "Register Shape",
                "handler": window._on_register_shape_from_selection,
                "shortcut": None,
                "shortcut_category": "Plugins",
                "group": "plugins",
                "order": 10,
                "enabled": window._can_register_shape_from_selection,
            },
            {
                "action_id": "shape_manager.register_shapes",
                "label": "Register Shapes",
                "handler": window._on_register_shape_merge_from_selection,
                "shortcut": None,
                "shortcut_category": "Plugins",
                "group": "plugins",
                "order": 20,
                "enabled": window._can_register_shape_from_selection,
            },
            {
                "action_id": "shape_manager.convert_to_vector",
                "label": "Convert to Vector",
                "handler": window._on_convert_shape_to_vector,
                "shortcut": None,
                "shortcut_category": "Plugins",
                "group": "plugins",
                "order": 30,
                "enabled": window._can_convert_shape_to_vector,
            },
            {
                "action_id": "vector.fill_to_selection",
                "label": "Fill to Selection",
                "handler": window._on_vector_fill_to_selection,
                "shortcut": None,
                "shortcut_category": "Edit",
                "group": "plugins",
                "order": 35,
                "enabled": window._can_vector_fill_to_selection,
            },
            {
                "action_id": "edit.deselect",
                "label": "&Deselect",
                "handler": window._on_deselect,
                "shortcut": "Ctrl+D",
                "shortcut_category": "Edit",
                "group": "edit",
                "order": 70,
            },
            {
                "action_id": "view.zoom_in",
                "label": "Zoom &In",
                "handler": window._on_zoom_in,
                "shortcut": "Ctrl+=",
                "shortcut_category": "View",
                "group": "view",
                "order": 10,
            },
            {
                "action_id": "view.zoom_out",
                "label": "Zoom &Out",
                "handler": window._on_zoom_out,
                "shortcut": "Ctrl+-",
                "shortcut_category": "View",
                "group": "view",
                "order": 20,
            },
            {
                "action_id": "view.zoom_reset",
                "label": "&Reset Zoom",
                "handler": window._on_zoom_reset,
                "shortcut": "Ctrl+0",
                "shortcut_category": "View",
                "group": "view",
                "order": 30,
            },
            {
                "action_id": "view.toggle_grid",
                "label": "Toggle &Grid",
                "handler": window._on_toggle_grid,
                "shortcut": "G",
                "shortcut_category": "View",
                "group": "view",
                "order": 40,
            },
            {
                "action_id": "view.toggle_rulers",
                "label": "Toggle &Rulers",
                "handler": window._on_toggle_rulers,
                "shortcut": "R",
                "shortcut_category": "View",
                "group": "view",
                "order": 50,
            },
            {
                "action_id": "edit.canvas_size",
                "label": "Canvas &Size...",
                "handler": window._on_canvas_resize,
                "shortcut": None,
                "shortcut_category": "Edit",
                "group": "canvas",
                "order": 10,
            },
        ]
        for entry in defaults:
            action_id = entry.get("action_id")
            if isinstance(action_id, str) and action_id in window._canvas_context_entries:
                continue
            window.register_canvas_context_action(
                action_id if isinstance(action_id, str) else None,
                label=entry.get("label"),
                handler=entry.get("handler"),
                shortcut=entry.get("shortcut"),
                shortcut_category=entry.get("shortcut_category", "Canvas"),
                shortcut_description=entry.get("shortcut_description"),
                group=entry.get("group", "custom"),
                order=entry.get("order", 100),
                enabled=entry.get("enabled"),
            )

    def on_requested(self, payload: Any) -> None:
        global_pos: QPoint | None = None
        if isinstance(payload, Mapping):
            self._window._last_canvas_context = dict(payload)
            candidate = payload.get("global_pos")
            if isinstance(candidate, QPoint):
                global_pos = candidate
        elif isinstance(payload, QPoint):
            self._window._last_canvas_context = {"global_pos": payload}
            global_pos = payload
        if global_pos is None:
            return
        self._window._show_canvas_context_menu(global_pos)

    def show_menu(self, global_pos: QPoint) -> None:
        window = self._window
        entries = list(window._canvas_context_entries.values())
        if not entries:
            return
        entries.sort(key=window._canvas_context_sort_key)
        menu = self._menu_factory(window)
        last_group: str | None = None
        shared_actions = set(window._shortcut_actions.values())
        for entry in entries:
            action = window._resolve_canvas_context_action(entry)
            if action is None:
                continue
            if last_group is not None and entry.group != last_group:
                menu.addSeparator()
            if entry.enabled and action not in shared_actions:
                try:
                    action.setEnabled(bool(entry.enabled()))
                except Exception:
                    logger.debug("Context menu enabled check failed", exc_info=True)
            menu.addAction(action)
            last_group = entry.group
        if menu.actions():
            menu.exec(global_pos)

    def sort_key(self, entry: CanvasContextEntry) -> tuple[int, int, str]:
        try:
            group_index = self._window._canvas_context_group_order.index(entry.group)
        except ValueError:
            group_index = 999
        label = entry.label or entry.action_id or ""
        return (group_index, entry.order, label.lower())

    def resolve_action(self, entry: CanvasContextEntry) -> QAction | None:
        window = self._window
        if entry.action_id:
            action = window._shortcut_actions.get(entry.action_id)
            if action is not None:
                return cast(QAction, action)
            if entry.label:
                action = window._mk_action(
                    entry.label,
                    entry.handler,
                    entry.shortcut,
                    action_id=entry.action_id,
                    shortcut_category=entry.shortcut_category,
                    shortcut_description=entry.shortcut_description,
                )
                action = cast(QAction, action)
                entry.action = action
                return action
            return None
        if entry.action is not None:
            return entry.action
        if not entry.label:
            return None
        entry.action = window._mk_action(
            entry.label,
            entry.handler,
            entry.shortcut,
        )
        entry.action = cast(QAction, entry.action)
        return entry.action
