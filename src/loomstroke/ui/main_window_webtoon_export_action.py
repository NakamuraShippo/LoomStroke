"""``File > Export Webtoon...`` action for MainWindow (CT-12 W1/W2, spec 12 §6).

Pages (summaries only — no hydration) -> segments -> dialog with the
virtual preview (thumbnails rendered lazily per visible page) ->
destination folder -> :func:`run_webtoon_export`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from ..core.webtoon.webtoon_segment import WebtoonSegment
from ..io.layer_io import DocumentMetadata
from .dialogs.export_webtoon_dialog import ExportWebtoonContext, ExportWebtoonDialog
from .preflight_export_gate import PreflightExportGate
from .translations import translate_with_fallback
from .webtoon_export_runner import run_webtoon_export
from .webtoon_preview_renderer import WebtoonPreviewRenderer

logger = logging.getLogger(__name__)

__all__ = ["MainWindowWebtoonExportAction"]


def _ts(text: str) -> str:
    return translate_with_fallback("ExportWebtoonDialog", text)


class MainWindowWebtoonExportAction:
    """Run the Export Webtoon dialog and write the package."""

    def __init__(
        self,
        window: Any,
        *,
        document_metadata: Callable[[], DocumentMetadata] | None = None,
        dialog_factory: Callable[..., Any] | None = None,
        preview_factory: Callable[..., Any] | None = None,
        file_dialog: Callable[[], Any] | None = None,
        dialog_type: Callable[[], Any] | None = None,
        message_box: Callable[[], Any] | None = None,
        exporter: Callable[..., Any] | None = None,
        export_gate: Any | None = None,
    ) -> None:
        self._window = window
        self._document_metadata = document_metadata or self._fallback_document_metadata
        self._dialog_factory = dialog_factory or ExportWebtoonDialog
        self._preview_factory = preview_factory or WebtoonPreviewRenderer
        self._file_dialog = file_dialog or (lambda: QFileDialog)
        self._dialog_type = dialog_type or (lambda: QDialog)
        self._message_box = message_box or (lambda: QMessageBox)
        self._exporter = exporter or run_webtoon_export
        self._export_gate = export_gate or PreflightExportGate(
            window, document_metadata=self._document_metadata
        )

    def run(self) -> bool:
        """Return ``True`` when an export was started."""
        window = self._window
        pages = getattr(getattr(window, "_host_api", None), "pages", None)
        list_pages = getattr(pages, "list_pages", None)
        summaries = list(list_pages() or []) if callable(list_pages) else []
        if not summaries:
            self._warn("No pages are available to export.")
            return False
        metadata = self._document_metadata()
        segments = tuple(self._segment_for(summary, metadata) for summary in summaries)
        context = ExportWebtoonContext(
            segments=segments,
            names_by_id={
                str(summary.id): str(getattr(summary, "name", "") or summary.id)
                for summary in summaries
            },
            document_name=self._document_name(),
        )
        preview = self._preview_factory(pages, metadata)
        try:
            dialog = self._dialog_factory(
                context=context, thumbnail_provider=preview.thumbnail, parent=window
            )
            accepted = dialog.exec() == self._dialog_type().DialogCode.Accepted
            plan = dialog.export_plan() if accepted else None
        finally:
            release = getattr(preview, "release", None)
            if callable(release):
                release()
        if plan is None:
            return False
        # CT-16: fatal preflight issues need one explicit confirmation.
        if not self._export_gate.allow("Export Webtoon"):
            return False
        document_path = getattr(window, "_document_path", None)
        start = str(Path(document_path).parent) if document_path else ""
        directory = self._file_dialog().getExistingDirectory(
            window, _ts("Choose Output Folder"), start
        )
        if not directory:
            return False
        self._exporter(window, pages, metadata, plan, destination_dir=Path(directory))
        return True

    # -- helpers --------------------------------------------------------------------

    @staticmethod
    def _segment_for(summary: Any, metadata: DocumentMetadata) -> WebtoonSegment:
        canvas = getattr(summary, "canvas", None)
        width = int(getattr(canvas, "width", 0) or metadata.width)
        height = int(getattr(canvas, "height", 0) or metadata.height)
        return WebtoonSegment(
            page_id=str(summary.id), source_width=max(1, width), source_height=max(1, height)
        )

    def _document_name(self) -> str:
        document_path = getattr(self._window, "_document_path", None)
        return Path(document_path).stem if document_path else "untitled"

    def _fallback_document_metadata(self) -> DocumentMetadata:
        host_api = getattr(self._window, "_host_api", None)
        canvas = getattr(host_api, "canvas", None) if host_api else None
        return DocumentMetadata(
            width=int(getattr(canvas, "_width", 1) or 1),
            height=int(getattr(canvas, "_height", 1) or 1),
        )

    def _warn(self, text: str) -> None:
        self._message_box().warning(self._window, _ts("Export Webtoon"), _ts(text))
