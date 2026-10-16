"""Release a MainWindow document session while keeping its workspace reusable."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor


def close_document_session(window: Any) -> None:
    """End tool work, close the core document, and drop UI-held document data."""
    service = window._get_document_service()
    manager = getattr(window, "tool_manager", None)
    active_tool = manager.get_active_tool() if manager is not None else None
    _cancel_pending_tool_work(manager)
    if active_tool is not None:
        manager.deactivate_tool()
    try:
        canvas_widget = getattr(window, "canvas", None)
        release = getattr(canvas_widget, "close_document_resources", None)
        if callable(release):
            release()

        close = getattr(service, "close_document", None)
        if callable(close):
            close()
        else:
            _reset_compatibility_canvas(window, service)

        _clear_tool_document_results(manager)

        # A reset can emit composite/thumbnail requests synchronously. Drop
        # them after the reset so an already queued idle callback cannot put
        # the previous document's preview back into the StartPage session.
        preview_sync = getattr(window, "_preview_sync", None)
        if callable(preview_sync):
            preview_sync().reset_pending_sync()
        window._is_canvas_stroke_in_progress = False
        preview = getattr(window, "_preview_panel", None)
        if preview is not None:
            preview.clear_preview()

        panel = getattr(window, "_layer_panel", None)
        if panel is not None:
            timer = getattr(panel, "_refresh_timer", None)
            if timer is not None:
                timer.stop()
            panel._selected_layer_id = None
            panel._thumbnail_cache.clear()
            panel._on_refresh_timeout()
    finally:
        # Re-enable the same tool with a new session, so no queued pointer
        # work or tool undo state can carry into the next document.
        if active_tool is not None:
            manager.activate_tool(active_tool.id)


def _cancel_pending_tool_work(manager: Any) -> None:
    for state in getattr(manager, "_tools", {}).values():
        tool = state.plugin
        if hasattr(tool, "_auto_run_pending"):
            tool._auto_run_pending = False
            tool._pending_auto_apply_operation = None
    session = getattr(manager, "_session", None)
    if session is not None:
        # qasync normally awaits cancellation on a later event-loop turn.
        # Mark old work cancelled before creating the next document session.
        session.task_registry.cancel_all()


def _clear_tool_document_results(manager: Any) -> None:
    # Selection tools keep document-sized candidate masks even while
    # inactive. Use their existing reset hook before enable can install
    # an overlay from the closed document again.
    for state in getattr(manager, "_tools", {}).values():
        for binding in (
            "_last_selection_inpaint_mask_asset_id",
            "_last_selection_inpaint_mask_layer_id",
        ):
            if hasattr(state.plugin, binding):
                setattr(state.plugin, binding, None)
        clear = getattr(state.plugin, "clear_run_state", None)
        if callable(clear):
            clear()
        clear_package = getattr(state.plugin, "set_manga_mask_assistant_package", None)
        if callable(clear_package):
            clear_package(None)


def _reset_compatibility_canvas(window: Any, service: Any) -> None:
    """Support older/custom document services without extending HostAPI."""
    canvas = getattr(getattr(window, "host", None), "canvas", None)
    reset = getattr(canvas, "reset_document", None)
    if callable(reset):
        settings = window._settings
        reset(
            width=settings.get("canvas/width", 1920),
            height=settings.get("canvas/height", 1080),
            dpi=settings.get("canvas/dpi", 96),
            background=QColor(255, 255, 255),
        )
    reset_state = getattr(service, "reset_document_state", None)
    if callable(reset_state):
        reset_state()
