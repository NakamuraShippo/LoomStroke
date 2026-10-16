"""Edit, selection, and Shape Manager action coordination for MainWindow."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, cast

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget

from .qt_clipboard_provider import QtClipboardProvider
from .system_clipboard_bridge import SystemClipboardBridge
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)

#: CT-19 reason codes -> status text (MainWindow domain).
_PASTE_MESSAGES: dict[str, str] = {
    "ok": "Pasted from the system clipboard.",
    "clipboard_custom_invalid_fallback_image": (
        "Pasted from the system clipboard as pixels (editable data was invalid)."
    ),
    "clipboard_unsupported": "The system clipboard has no image to paste.",
    "clipboard_custom_invalid": "The clipboard data from LoomStroke could not be read.",
    "clipboard_image_too_large": "The clipboard image is too large to paste.",
    "clipboard_image_decode_failed": "The clipboard image could not be decoded.",
    "clipboard_paste_rejected": "The canvas did not accept the paste.",
    "canvas_unavailable": "No canvas is available to paste into.",
}


class MainWindowEditShapeActionsCoordinator:
    """Coordinate MainWindow edit menu bridges without owning tool/plugin logic."""

    def __init__(
        self,
        window: Any,
        *,
        message_box: Callable[[], Any],
        application_clipboard: Callable[[], Any],
    ) -> None:
        self._window = window
        self._message_box = message_box
        self._application_clipboard = application_clipboard
        self._system_clipboard_bridge: SystemClipboardBridge | None = None

    def _host_api(self) -> Any | None:
        return getattr(self._window, "_host_api", None)

    def _canvas(self) -> Any | None:
        host_api = self._host_api()
        return getattr(host_api, "canvas", None) if host_api else None

    def _tool_manager(self) -> Any | None:
        tool_manager = getattr(self._window, "tool_manager", None)
        if tool_manager is not None:
            return tool_manager
        host_api = self._host_api()
        return getattr(host_api, "_tool_manager", None) if host_api else None

    def dispatch_selection_action(self, action_key: str) -> None:
        tool_manager = self._tool_manager()
        if tool_manager is None:
            return
        selection_tool_id = "loomstroke.basic.selection.rect"
        target_tool_id: str | None = None
        has_tool = getattr(tool_manager, "has_tool", None)
        if callable(has_tool) and has_tool(selection_tool_id):
            target_tool_id = selection_tool_id
        else:
            getter = getattr(tool_manager, "get_active_tool", None)
            active = getter() if callable(getter) else None
            target_tool_id = getattr(active, "id", None) if active else None
        if not target_tool_id:
            return
        handled = False
        try:
            handled = bool(tool_manager.dispatch_tool_action(target_tool_id, action_key, None))
        except Exception:
            logger.debug("Selection action failed: %s", action_key, exc_info=True)
            return

        if handled:
            return

        # Fallback: if the selection tool did not emit commands, try the active tool.
        if target_tool_id == selection_tool_id:
            getter = getattr(tool_manager, "get_active_tool", None)
            active = getter() if callable(getter) else None
            active_tool_id = getattr(active, "id", None) if active else None
            if active_tool_id and active_tool_id != target_tool_id:
                try:
                    tool_manager.dispatch_tool_action(active_tool_id, action_key, None)
                except Exception:
                    logger.debug(
                        "Active tool action fallback failed: %s", action_key, exc_info=True
                    )

    def on_cut(self) -> None:
        self._window._dispatch_selection_action("selection.cut")

    def on_copy(self) -> None:
        self._window._dispatch_selection_action("selection.copy")
        self._window._sync_to_system_clipboard()

    def system_clipboard_bridge(self) -> SystemClipboardBridge:
        if self._system_clipboard_bridge is None:
            self._system_clipboard_bridge = SystemClipboardBridge(
                self._host_api, QtClipboardProvider(self._application_clipboard)
            )
        return self._system_clipboard_bridge

    def on_paste_from_system_clipboard(self) -> bool:
        """CT-19: paste the OS clipboard (LoomStroke envelope first, else image)."""
        try:
            pasted, reason = self.system_clipboard_bridge().paste_from_system()
        except Exception:
            logger.debug("system clipboard paste failed", exc_info=True)
            pasted, reason = False, "clipboard_image_decode_failed"
        self._show_status(_PASTE_MESSAGES.get(reason, reason))
        return pasted

    def _show_status(self, text: str) -> None:
        status_bar = getattr(self._window, "statusBar", None)
        bar = status_bar() if callable(status_bar) else None
        show = getattr(bar, "showMessage", None)
        if callable(show):
            show(translate_with_fallback("MainWindow", text), 4000)

    def sync_to_system_clipboard(self) -> None:
        """Copy the internal selection clipboard image to the system clipboard.

        CT-19: publishes the editable LoomStroke envelope (mask / bounds /
        DPI) plus the flattened image; falls back to the historical
        image-only mirror when the bridge cannot publish."""
        try:
            outcome = self.system_clipboard_bridge().publish_internal_selection()
            if outcome in {"published", "published_image_only"}:
                return
        except Exception:
            logger.debug("editable clipboard publish failed", exc_info=True)
        try:
            host_api = self._host_api()
            clipboard = getattr(host_api, "selection_clipboard", None) if host_api else None
            if clipboard is None or clipboard.is_empty():
                return
            payload = clipboard.get()
            if payload is None:
                return
            image = payload.image
            if isinstance(image, QImage) and not image.isNull():
                self._application_clipboard().setImage(image)
        except Exception:
            logger.debug("Failed to sync to system clipboard", exc_info=True)

    def on_paste(self) -> None:
        self._window._dispatch_selection_action("selection.paste")

    def on_select_all(self) -> None:
        self._window._dispatch_selection_action("selection.select_all")

    def on_deselect(self) -> None:
        self._window._dispatch_selection_action("selection.deselect")

    def on_select_all_vectors(self) -> None:
        self._window._dispatch_selection_action("selection.vector_select_all")

    def shape_manager_plugin(self) -> Any | None:
        registry = getattr(self._window, "_plugin_registry", None)
        if registry is None:
            return None
        try:
            return registry.get("loomstroke.shape_manager")
        except Exception:
            logger.debug("Failed to resolve Shape Manager plugin", exc_info=True)
            return None

    def get_shape_manager_panel(self) -> QWidget | None:
        get_panel_dock = getattr(self._window, "get_panel_dock", None)
        dock = (
            get_panel_dock("loomstroke.shape_manager.panel") if callable(get_panel_dock) else None
        )
        if dock is not None:
            widget = dock.widget()
            if isinstance(widget, QWidget):
                return widget

        plugin_getter = getattr(self._window, "_shape_manager_plugin", None)
        plugin = plugin_getter() if callable(plugin_getter) else self.shape_manager_plugin()
        ensure = getattr(self._window, "_ensure_plugin_activated", None)
        if not plugin or not callable(ensure) or not ensure(plugin):
            return None
        creator = getattr(plugin, "create_widget", None)
        if not callable(creator):
            return None
        try:
            widget = creator(self._window)
        except Exception:
            logger.debug("Failed to create Shape Manager widget", exc_info=True)
            return None
        return widget if isinstance(widget, QWidget) else None

    def has_vector_selection(self) -> bool:
        canvas = self._canvas()
        if canvas is None:
            return False
        selection = getattr(canvas, "_vector_selection", None)
        if not isinstance(selection, dict):
            return False
        items = selection.get("items")
        if isinstance(items, list):
            return any(isinstance(item, dict) and isinstance(item.get("id"), str) for item in items)
        ids = selection.get("ids")
        if isinstance(ids, list):
            return any(isinstance(vec_id, str) for vec_id in ids)
        vec_id = selection.get("id")
        return isinstance(vec_id, str)

    def can_register_shape_from_selection(self) -> bool:
        plugin_getter = getattr(self._window, "_shape_manager_plugin", None)
        plugin = plugin_getter() if callable(plugin_getter) else self.shape_manager_plugin()
        selection_checker = getattr(self._window, "_has_vector_selection", None)
        has_selection = (
            selection_checker() if callable(selection_checker) else self.has_vector_selection()
        )
        return plugin is not None and bool(has_selection)

    def can_convert_shape_to_vector(self) -> bool:
        panel = self._shape_manager_panel_from_window()
        if panel is None:
            return False
        checker = getattr(panel, "can_convert_selected_basic_shapes", None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            logger.debug("Shape conversion availability check failed", exc_info=True)
            return False

    def on_register_shape_from_selection(self) -> None:
        panel = self._shape_manager_panel_from_window()
        if panel is None:
            self._show_shape_manager_message("Shape Manager is not available.")
            return
        handler = getattr(panel, "register_selection", None) or getattr(
            panel, "_on_register_selection", None
        )
        if callable(handler):
            handler()
            return
        self._show_shape_manager_message("Shape Manager command unavailable.")

    def on_register_shape_merge_from_selection(self) -> None:
        panel = self._shape_manager_panel_from_window()
        if panel is None:
            self._show_shape_manager_message("Shape Manager is not available.")
            return
        handler = getattr(panel, "register_selection_merged", None) or getattr(
            panel, "_on_register_selection_merged", None
        )
        if callable(handler):
            handler()
            return
        self._show_shape_manager_message("Shape Manager command unavailable.")

    def on_convert_shape_to_vector(self) -> None:
        panel = self._shape_manager_panel_from_window()
        if panel is None:
            self._show_shape_manager_message("Shape Manager is not available.")
            return
        handler = getattr(panel, "convert_selected_basic_shapes_to_vector", None)
        if callable(handler):
            handler()
            return
        self._show_shape_manager_message("Shape Manager command unavailable.")

    def _shape_manager_panel_from_window(self) -> Any | None:
        panel_getter = getattr(self._window, "_get_shape_manager_panel", None)
        return panel_getter() if callable(panel_getter) else self.get_shape_manager_panel()

    def _show_shape_manager_message(self, message: str) -> None:
        self._message_box().information(
            self._window,
            self._window._ts("Shape Manager"),
            self._window._ts(message),
        )

    def can_vector_fill_to_selection(self) -> bool:
        """Check if the selected vector object has closed (fillable) paths."""
        selection_checker = getattr(self._window, "_has_vector_selection", None)
        has_selection = (
            selection_checker() if callable(selection_checker) else self.has_vector_selection()
        )
        if not has_selection:
            return False
        canvas = self._canvas()
        if canvas is None:
            return False
        selection = getattr(canvas, "_vector_selection", None)
        if not isinstance(selection, dict):
            return False
        layer_id = selection.get("layer_id")
        vec_id = self.get_first_selected_vector_id(selection)
        if not layer_id or not vec_id:
            return False
        return self._selected_vector_has_fillable_path(canvas, layer_id, vec_id)

    def _selected_vector_has_fillable_path(
        self,
        canvas: Any,
        layer_id: str,
        vec_id: str,
    ) -> bool:
        layers = getattr(canvas, "_layers", [])
        for layer in layers:
            if getattr(layer, "id", None) != layer_id:
                continue
            for obj in getattr(layer, "vector_objects", []):
                if getattr(obj, "id", None) != vec_id:
                    continue
                if self._vector_object_has_fillable_path(obj):
                    return True
        return False

    @staticmethod
    def _vector_object_has_fillable_path(obj: Any) -> bool:
        for path in getattr(obj, "paths", []):
            anchors = getattr(path, "anchors", None) or []
            if len(anchors) < 3:
                continue
            if getattr(path, "closed", False):
                return True
            fa = anchors[0].pos
            la = anchors[-1].pos
            if (fa[0] - la[0]) ** 2 + (fa[1] - la[1]) ** 2 < 0.25:
                return True
        return False

    @staticmethod
    def get_first_selected_vector_id(selection: dict[str, Any]) -> str | None:
        """Extract the first vector object ID from a vector selection dict."""
        items = selection.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    return cast("str", item["id"])
        ids = selection.get("ids")
        if isinstance(ids, list):
            for vid in ids:
                if isinstance(vid, str):
                    return vid
        vid = selection.get("id")
        return vid if isinstance(vid, str) else None

    def on_vector_fill_to_selection(self) -> None:
        """Convert the selected vector object's fill area to a raster selection."""
        canvas = self._canvas()
        if canvas is None:
            return
        selection = getattr(canvas, "_vector_selection", None)
        if not isinstance(selection, dict):
            return
        layer_id = selection.get("layer_id")
        vec_id = self.get_first_selected_vector_id(selection)
        if not layer_id or not vec_id:
            return

        from loomstroke.plugins.vector_tools.fill_to_selection_command import (  # noqa: PLC0415
            VectorFillToSelectionCommand,
        )

        cmd = VectorFillToSelectionCommand(layer_id, vec_id)
        canvas.submit(cmd)
