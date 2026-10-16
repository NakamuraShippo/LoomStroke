"""Dialog-free adapters around the application's existing export runners."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import numpy as np
from PySide6.QtWidgets import QMessageBox

from loomstroke.host_api import SaveOptions
from loomstroke.io.layer_io import DocumentMetadata
from loomstroke.ui.main_window_document_workflow import MainWindowDocumentWorkflowCoordinator
from loomstroke.ui.offscreen_page_renderer import OffscreenPageRenderer

from .extension_services import get_services
from .types import McpStructuredError, ReasonCode


class ExportMessages:
    StandardButton = QMessageBox.StandardButton

    def __init__(self, overwrite: bool = False) -> None:
        self.overwrite = overwrite
        self.messages: list[str] = []
        self.warnings: list[dict[str, Any]] = []

    def information(self, _parent: Any, _title: str, message: str) -> None:
        self.messages.append(message)

    def warning(self, _parent: Any, _title: str, message: str) -> None:
        self.messages.append(message)

    critical = warning

    def question(self, *_args: Any, **_kwargs: Any) -> Any:
        return self.StandardButton.Yes if self.overwrite else self.StandardButton.No

    def showMessage(self, message: str, _duration: int = 0) -> None:  # noqa: N802
        self.messages.append(message)


class ExportProgress:
    def __init__(self, _total: int) -> None:
        self.value = 0

    def wasCanceled(self) -> bool:  # noqa: N802
        return False

    def setLabelText(self, _text: str) -> None:  # noqa: N802
        return None

    def setValue(self, value: int) -> None:  # noqa: N802
        self.value = value

    def close(self) -> None:
        return None


def document_metadata(service: Any) -> DocumentMetadata:
    dependencies = get_services(service)
    getter = dependencies.get("document_metadata")
    if callable(getter):
        result = getter()
    else:
        document = dependencies.get("document_service")
        result = document._build_document_metadata(SaveOptions()) if document else None
    if not isinstance(result, DocumentMetadata):
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "Full document metadata is required for publication export.",
        )
    return result


def page_renderer(service: Any) -> tuple[Callable[..., np.ndarray], Callable[[], None]]:
    callback = get_services(service).get("render_page")
    if callable(callback):
        return callback, lambda: None
    renderer = OffscreenPageRenderer()
    return renderer.render, renderer.release


def layered_export(service: Any, path: Any, format_id: str) -> dict[str, Any]:
    messages = ExportMessages()
    canvas = service.canvas_service
    window = SimpleNamespace(
        _host_api=SimpleNamespace(canvas=canvas),
        _ts=lambda text: text,
        statusBar=lambda: messages,
        _show_svg_export_omission_warning=lambda _name, count: messages.warnings.append(
            {"code": "svg_unsupported_layers_omitted", "count": count}
        ),
    )
    coordinator = MainWindowDocumentWorkflowCoordinator(
        window,
        new_document_dialog=lambda: None,
        file_dialog=lambda: None,
        message_box=lambda: messages,
        progress_dialog=lambda: None,
        dialog_type=lambda: None,
    )
    if format_id == "svg":
        success = coordinator.export_svg(path)
    else:
        from loomstroke.core.commands import BlendMode  # noqa: PLC0415
        from loomstroke.io.file_io import FileIO  # noqa: PLC0415

        success = coordinator.do_export_psd_impl(path, canvas, BlendMode, FileIO)
        if any(
            layer.layer_type in {"effect", "ai_effect"} or layer.layer_mask is not None
            for layer in canvas._flatten_layers()
        ):
            messages.warnings.append(
                {
                    "code": "psd_feature_subset",
                    "message": "The GUI PSD exporter writes rasterized layer content; "
                    "live filters, AI Effect layers, and masks are not preserved as "
                    "editable PSD features.",
                }
            )
    if not success:
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED,
            "The layered/vector export could not be written.",
            details={"format": format_id, "messages": messages.messages},
        )
    return {
        "status": "warning" if messages.warnings else "success",
        "path": str(path),
        "bytesWritten": path.stat().st_size,
        "issues": messages.warnings,
    }
