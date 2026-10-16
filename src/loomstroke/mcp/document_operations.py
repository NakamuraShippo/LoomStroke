"""Document lifecycle support for MCP without an attached MainWindow."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QApplication

from ..core.canvas_service_simple import SimpleCanvasService
from ..core.document_service import DocumentServiceImpl
from ..core.document_snapshot_builder import DocumentSnapshotBuilder
from ..core.event_bus import EventBus
from ..core.events import Events
from ..core.page_service import PageServiceImpl
from ..host_api import LoadOptions, SaveOptions
from ..io.file_io import FileIO
from ..io.layer_io import LayerIO
from .types import McpStructuredError, ReasonCode


def backend_failure(operation: str) -> McpStructuredError:
    return McpStructuredError(
        ReasonCode.BACKEND_FAILED,
        f"Document {operation} failed; the current document was not replaced.",
        details={"operation": operation},
    )


def preflight_open(document: Any, path: Path) -> None:
    """Validate the archive and canvas conversion before changing the document."""
    layer_io = getattr(document, "_layer_io", None) or LayerIO()
    _metadata, entries = layer_io.read_page_directory(path, validate_manifest=True, strict=True)
    if not entries:
        raise ValueError("document contains no pages")
    page = layer_io.load_single_page_snapshot(path, entries[0].page_id, strict=True)
    if page is None:
        raise ValueError("document active page is missing")
    DocumentSnapshotBuilder.snapshots_to_simple_layers(page.layers)


async def call_handler(
    handler: Any, operation: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    result = handler(operation, arguments)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, Mapping):
        raise backend_failure(operation)
    return dict(result)


def document_handler(service: Any) -> Any:
    callback = getattr(service, "document_operation_handler", None)
    if callable(callback):
        return callback
    if (
        str(getattr(service, "mode", "")).startswith("live")
        or getattr(service, "_snapshot_provider", None) is not None
    ):
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "The live document workflow is not attached.",
        )
    controller = getattr(service, "_mcp_headless_document_controller", None)
    if controller is None or controller.canvas is not service.canvas_service:
        controller = HeadlessMcpDocumentController(service)
        service._mcp_headless_document_controller = controller
    return controller.handle


class HeadlessMcpDocumentController:
    """Owns path/dirty state while using the actual canvas and DocumentService."""

    def __init__(self, service: Any) -> None:
        self._service = service
        self.canvas = service.canvas_service
        self._document = getattr(getattr(service, "host_api", None), "document", None)
        self._bus = getattr(self._document, "_event_bus", None) or getattr(
            self.canvas, "_event_bus", None
        )
        self._path: Path | None = None
        self._present = self.canvas is not None
        # No path/baseline observer owned this canvas before attachment.
        # Even an empty history cannot prove raw changes were saved.
        self._dirty = self._present
        self._application: QGuiApplication | None = None
        self._handlers: list[Any] = []
        self._tokens: list[Any] = []
        if self.canvas is not None:
            self._connect()

    def _connect(self) -> None:
        if not isinstance(self.canvas, SimpleCanvasService):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "Document lifecycle requires the SimpleCanvasService implementation.",
            )
        self._bus = self._bus or EventBus()
        if getattr(self.canvas, "_event_bus", None) is None:
            self.canvas.set_event_bus(self._bus)
        pages = self._service.page_service
        if pages is None:
            pages = PageServiceImpl(self.canvas, self._bus)
            self._service.page_service = pages
        if self._document is None:
            self._document = DocumentServiceImpl(self.canvas, LayerIO(), FileIO(), self._bus, pages)
        for event, handler in (
            (Events.DOC_CHANGED, self._modified),
            (Events.DOC_CREATED, self._created),
            (Events.DOC_LOADED, self._loaded),
            (Events.DOC_CLOSED, self._closed),
            (Events.DOC_SAVE_OUTCOME, self._saved),
        ):
            self._handlers.append(handler)
            self._tokens.append(self._bus.on(event, handler))

    def _ensure_canvas(self, arguments: Mapping[str, Any]) -> None:
        if self.canvas is not None:
            return
        application = QCoreApplication.instance()
        if application is not None and not isinstance(application, QGuiApplication):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "Document editing requires a Qt GUI application.",
            )
        if application is None:
            application = QApplication([])
        self._application = application
        self.canvas = SimpleCanvasService(
            width=int(arguments.get("width", 1)),
            height=int(arguments.get("height", 1)),
        )
        self._service.canvas_service = self.canvas
        self._connect()

    def state(self) -> dict[str, Any]:
        ref = self.canvas.current_document() if self.canvas is not None else None
        return {
            "available": QCoreApplication.instance() is None
            or isinstance(QCoreApplication.instance(), QGuiApplication),
            "present": self._present,
            "dirty": self._dirty,
            "path": str(self._path) if self._path else None,
            "documentId": getattr(ref, "id", None),
            "width": getattr(self.canvas, "_width", None),
            "height": getattr(self.canvas, "_height", None),
            "dpi": getattr(self.canvas, "_dpi", None),
            "saveInFlight": bool(getattr(self._document, "_save_in_progress", False)),
        }

    async def handle(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "state":
            return self.state()
        if operation == "open":
            preflight_open(self._document, Path(arguments["path"]))
        if operation in {"new", "open"}:
            self._ensure_canvas(arguments)
        if operation == "new":
            self.canvas.reset_document(
                width=arguments["width"],
                height=arguments["height"],
                dpi=arguments["dpi"],
                background=QColor(*arguments["backgroundColor"]),
            )
            self._document.reset_document_state()
            self._bus.emit(Events.DOC_CREATED, dict(arguments))
        elif operation == "open":
            if not self._document.load(arguments["path"], LoadOptions()):
                raise backend_failure(operation)
        elif operation == "save":
            if not await self._document.save_async(arguments["path"], SaveOptions()):
                raise backend_failure(operation)
        elif operation == "close":
            self._bus.emit(Events.DOC_CLOSED, {"source": "mcp"})
        else:
            raise McpStructuredError(ReasonCode.INVALID_ARGUMENT, "Unknown document operation.")
        return self.state()

    def _modified(self, _: Any) -> None:
        self._dirty = True

    def _created(self, _: Any) -> None:
        self._path = None
        self._dirty = False
        self._present = True

    def _loaded(self, payload: Mapping[str, Any]) -> None:
        self._path = Path(payload["path"])
        self._dirty = False
        self._present = True

    def _closed(self, _: Any) -> None:
        self._path = None
        self._dirty = False
        self._present = False

    def _saved(self, payload: Mapping[str, Any]) -> None:
        if payload.get("autosave") or not payload.get("save_same_document"):
            return
        self._path = Path(payload["path"])
        if payload.get("save_covers_latest"):
            self._dirty = False
