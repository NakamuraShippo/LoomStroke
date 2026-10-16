"""Connect guarded MCP document operations to the existing window workflow."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..host_api import LoadOptions, SaveOptions
from ..mcp.document_operations import backend_failure, preflight_open
from ..mcp.types import McpStructuredError, ReasonCode
from .startpage_models import BackgroundSpec, StartPageCanvasSpec


class MainWindowMcpDocumentController:
    """Keeps the normal UI document state and authoritative save outcomes."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def state(self) -> dict[str, Any]:
        window = self._window
        document = window._get_document_service()
        canvas = getattr(getattr(window, "host", None), "canvas", None)
        ref = canvas.current_document() if canvas is not None else None
        path = getattr(window, "_document_path", None)
        return {
            "available": document is not None and canvas is not None,
            "present": bool(getattr(window, "_has_document", False)),
            "dirty": bool(getattr(window, "_document_dirty", False)),
            "path": str(path) if path else None,
            "documentId": getattr(ref, "id", None),
            "width": getattr(canvas, "_width", None),
            "height": getattr(canvas, "_height", None),
            "dpi": getattr(canvas, "_dpi", None),
            "saveInFlight": bool(getattr(window, "_save_async_in_flight", False))
            or bool(getattr(document, "_save_in_progress", False)),
        }

    async def handle(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "state":
            return self.state()
        window = self._window
        document = window._get_document_service()
        if document is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE, "Document service is unavailable."
            )
        window._mcp_document_operation_in_flight = True
        try:
            if operation == "new":
                window._apply_new_document(
                    StartPageCanvasSpec(
                        width_px=arguments["width"],
                        height_px=arguments["height"],
                        dpi=arguments["dpi"],
                        background=BackgroundSpec("solid", tuple(arguments["backgroundColor"])),
                        auto_fix_multiple_of_64=False,
                    )
                )
            elif operation == "open":
                path = Path(arguments["path"])
                preflight_open(document, path)
                if not document.load(path, LoadOptions()):
                    raise backend_failure(operation)
                window._document_path = path
                window._document_dirty = False
                window._update_window_title()
                window._set_document_present(True)
                window._last_file_dir = str(path.parent)
                window._add_recent_file(str(path))
            elif operation == "save":
                await self._save(document, Path(arguments["path"]))
            elif operation == "close":
                window._close_document()
            else:
                raise McpStructuredError(ReasonCode.INVALID_ARGUMENT, "Unknown document operation.")
        finally:
            window._mcp_document_operation_in_flight = False
        return self.state()

    async def _save(self, document: Any, path: Path) -> None:
        window = self._window
        # Reuse the existing workflow's final-outcome state machine, without
        # starting a detached task or invoking its modal failure fallback.
        from .main_window import _document_workflow_for  # noqa: PLC0415

        workflow = _document_workflow_for(window)
        workflow._last_manual_save_outcome = None
        workflow._pending_save_claim = None
        window._save_async_in_flight = True
        try:
            save = getattr(document, "save_async", None) or document.save
            saved = save(path, SaveOptions())
            if inspect.isawaitable(saved):
                saved = await saved
            if not saved:
                raise backend_failure("save")
            if workflow._last_manual_save_outcome is None and workflow._pending_save_claim is None:
                # Preserve compatibility with custom services without events.
                window._document_path = path
                window._document_dirty = False
                window._update_window_title()
            window._last_file_dir = str(path.parent)
        finally:
            window._save_async_in_flight = False
