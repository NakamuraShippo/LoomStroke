"""MainWindow context toolbar and tool cursor coordination."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid as is_valid

logger = logging.getLogger("loomstroke.ui.main_window")


class MainWindowContextToolbarCoordinator:
    """Own MainWindow-side context toolbar creation and cursor side effects."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def create_context_toolbar_manager(self) -> Any:
        """Create and wire the ContextToolbarManager."""
        window = self._window
        try:
            from loomstroke.ui.context_toolbar.manager import ContextToolbarManager  # noqa: PLC0415

            canvas_service = getattr(window.host, "canvas", None)
            if (
                window._canvas_frame is None
                or window.canvas is None
                or window.tool_manager is None
                or canvas_service is None
            ):
                return None
            mgr = ContextToolbarManager(
                ruler_frame=window._canvas_frame,
                canvas_widget=window.canvas,
                tool_manager=window.tool_manager,
                canvas_service=canvas_service,
                event_bus=window._event_bus,
            )
            window.canvas.set_context_toolbar_manager(mgr)
            try:
                from loomstroke.ui.context_toolbar.three_d_toolbar import (  # noqa: PLC0415
                    ThreeDLayerContextToolbarController,
                )

                window._three_d_context_toolbar_controller = ThreeDLayerContextToolbarController(
                    mgr,
                    canvas_service,
                    window._event_bus,
                )
            except Exception:
                logger.exception("Failed to register 3D layer context toolbar")
            # Subscribe to tool changes to update spec.
            window.tool_manager.tool_activated.connect(
                lambda tool_id: mgr.on_tool_activated(tool_id)
            )
            three_d_controller = getattr(window, "_three_d_context_toolbar_controller", None)
            if three_d_controller is not None:
                window.tool_manager.tool_activated.connect(
                    lambda _tool_id: three_d_controller.refresh()
                )
            # Seed with currently active tool (if any).
            active_id = getattr(window.tool_manager, "_active_tool_id", None)
            if active_id:
                mgr.on_tool_activated(active_id)
            if three_d_controller is not None:
                three_d_controller.refresh()
            logger.info("ContextToolbarManager initialized")
            return mgr
        except Exception:
            logger.exception("Failed to create ContextToolbarManager")
            return None

    def on_tool_cursor_activated(self, tool_id: str) -> None:
        del tool_id
        tool_manager = self._window.tool_manager
        descriptor = tool_manager.get_active_tool() if tool_manager is not None else None
        cursor_name = getattr(descriptor, "cursor", None) if descriptor is not None else None
        self.apply_canvas_tool_cursor(cursor_name)

    def on_tool_cursor_deactivated(self, tool_id: str) -> None:
        del tool_id
        self.apply_canvas_tool_cursor(None)

    def apply_canvas_tool_cursor(self, cursor_name: str | None) -> None:
        canvas = getattr(self._window, "canvas", None)
        if not isinstance(canvas, QWidget) or not is_valid(canvas):
            return
        cursor = self.cursor_from_tool_descriptor(cursor_name)
        if cursor is None:
            canvas.unsetCursor()
            return
        canvas.setCursor(cursor)

    @staticmethod
    def cursor_from_tool_descriptor(cursor_name: str | None) -> QCursor | None:
        token = (cursor_name or "").strip().lower().replace("-", "_")
        if not token or token in {"default", "none"}:
            return None
        shape_by_name = {
            "arrow": Qt.CursorShape.ArrowCursor,
            "crosshair": Qt.CursorShape.CrossCursor,
            "cross": Qt.CursorShape.CrossCursor,
            "pen": Qt.CursorShape.CrossCursor,
            "eraser": Qt.CursorShape.CrossCursor,
            "ibeam": Qt.CursorShape.IBeamCursor,
            "i_beam": Qt.CursorShape.IBeamCursor,
            "text": Qt.CursorShape.IBeamCursor,
            "move": Qt.CursorShape.SizeAllCursor,
        }
        shape = shape_by_name.get(token)
        return QCursor(shape) if shape is not None else None
