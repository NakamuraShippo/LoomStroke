"""``File > Export Digital Comic Package...`` action for MainWindow (CT-26).

Logical page order from the CT-06 export-order provider, roles / spread
sides from the book layout, dialog, preflight gate, destination chooser
(file for CBZ / EPUB, folder for the image package) and the runner.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from ..core.book_layout_service import BookLayoutService
from ..core.book_spread_layout import find_spread_for_page, resolve_spreads
from ..io.layer_io import DocumentMetadata
from .dialogs.export_digital_package_dialog import (
    ExportDigitalPackageContext,
    ExportDigitalPackageDialog,
    ExportDigitalPackagePlan,
)
from .digital_package_export_runner import run_digital_package_export
from .preflight_export_gate import PreflightExportGate
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)

__all__ = ["MainWindowDigitalPackageExportAction"]

_FILTERS: dict[str, tuple[str, str]] = {
    "cbz": ("Comic Book Archive (*.cbz)", ".cbz"),
    "epub3_fxl": ("EPUB Publication (*.epub)", ".epub"),
}


def _ts(text: str) -> str:
    return translate_with_fallback("ExportDigitalPackageDialog", text)


class MainWindowDigitalPackageExportAction:
    """Run the Export Digital Comic Package dialog and write the package."""

    def __init__(
        self,
        window: Any,
        *,
        document_metadata: Callable[[], DocumentMetadata] | None = None,
        dialog_factory: Callable[..., Any] | None = None,
        file_dialog: Callable[[], Any] | None = None,
        dialog_type: Callable[[], Any] | None = None,
        message_box: Callable[[], Any] | None = None,
        exporter: Callable[..., Any] | None = None,
        export_gate: Any | None = None,
    ) -> None:
        self._window = window
        self._document_metadata = document_metadata or self._fallback_document_metadata
        self._dialog_factory = dialog_factory or ExportDigitalPackageDialog
        self._file_dialog = file_dialog or (lambda: QFileDialog)
        self._dialog_type = dialog_type or (lambda: QDialog)
        self._message_box = message_box or (lambda: QMessageBox)
        self._exporter = exporter or run_digital_package_export
        self._export_gate = export_gate or PreflightExportGate(
            window, document_metadata=self._document_metadata
        )

    def run(self) -> bool:
        window = self._window
        pages = getattr(getattr(window, "_host_api", None), "pages", None)
        list_pages = getattr(pages, "list_pages", None)
        summaries = list(list_pages() or []) if callable(list_pages) else []
        if not summaries:
            self._message_box().warning(
                window,
                _ts("Export Digital Comic Package"),
                _ts("No pages are available to export."),
            )
            return False
        context = self._context(pages, summaries)
        dialog = self._dialog_factory(context=context, parent=window)
        if dialog.exec() != self._dialog_type().DialogCode.Accepted:
            return False
        plan = dialog.export_plan()
        if plan is None:
            return False
        if not self._export_gate.allow("Export Digital Comic Package"):
            return False
        destination = self._choose_destination(plan)
        if destination is None:
            return False
        self._exporter(window, pages, self._document_metadata(), plan, destination=destination)
        return True

    # -- helpers --------------------------------------------------------------------

    def _context(self, pages: Any, summaries: list[Any]) -> ExportDigitalPackageContext:
        page_ids = tuple(str(summary.id) for summary in summaries)
        export_order = getattr(pages, "export_page_ids", None)
        if callable(export_order):
            try:
                ordered = tuple(str(pid) for pid in export_order())
                if set(ordered) == set(page_ids):
                    page_ids = ordered
            except Exception:
                logger.debug("export order unavailable; using page order", exc_info=True)
        roles, sides, binding = self._book_attributes(pages, page_ids)
        return ExportDigitalPackageContext(
            page_ids=page_ids,
            names_by_id={
                str(summary.id): str(getattr(summary, "name", "") or summary.id)
                for summary in summaries
            },
            roles_by_id=roles,
            spread_sides_by_id=sides,
            binding=binding,
            document_name=self._document_name(),
        )

    def _book_attributes(
        self, pages: Any, page_ids: tuple[str, ...]
    ) -> tuple[dict[str, str], dict[str, str], str]:
        reader = getattr(pages, "get_page_metadata", None)
        metadata_by_id = {
            page_id: (reader(page_id) if callable(reader) else None) for page_id in page_ids
        }
        layout = self._book_layout()
        try:
            attributes = BookLayoutService().resolve_page_attributes_by_id(metadata_by_id)
        except Exception:
            logger.debug("page attributes unavailable", exc_info=True)
            attributes = {}
        roles = {page_id: str(attr.role) for page_id, attr in attributes.items()}
        sides: dict[str, str] = {}
        try:
            spreads = resolve_spreads(page_ids, layout, attributes or None)
            for page_id in page_ids:
                spread = find_spread_for_page(spreads, page_id)
                if spread is None:
                    continue
                if len(spread.page_ids()) < 2:
                    sides[page_id] = "center"
                else:
                    for slot in spread.slots:
                        if slot.page_id == page_id:
                            sides[page_id] = str(slot.physical_side)
        except Exception:
            logger.debug("spread sides unavailable", exc_info=True)
        binding = str(getattr(layout, "binding", "left") or "left")
        return roles, sides, binding

    def _book_layout(self) -> Any:
        get_service = getattr(self._window, "_get_document_service", None)
        service = get_service() if callable(get_service) else None
        resolve = getattr(service, "resolve_book_layout", None)
        if callable(resolve):
            try:
                return resolve()
            except Exception:
                logger.debug("book layout unavailable", exc_info=True)
        return None

    def _choose_destination(self, plan: ExportDigitalPackagePlan) -> Path | None:
        window = self._window
        document_path = getattr(window, "_document_path", None)
        base = Path(document_path).with_suffix("") if document_path else Path("untitled")
        if plan.format_id == "image_manifest_v1":
            directory = self._file_dialog().getExistingDirectory(
                window, _ts("Choose Output Folder"), str(base.parent) if document_path else ""
            )
            return Path(directory) if directory else None
        filter_text, suffix = _FILTERS[plan.format_id]
        path, _selected = self._file_dialog().getSaveFileName(
            window,
            _ts("Export Digital Comic Package"),
            str(base.with_suffix(suffix)),
            _ts(filter_text),
        )
        if not path:
            return None
        chosen = Path(path)
        return chosen if chosen.suffix else chosen.with_suffix(suffix)

    def _document_name(self) -> str:
        document_path = getattr(self._window, "_document_path", None)
        return Path(document_path).stem if document_path else "untitled"

    def _fallback_document_metadata(self) -> DocumentMetadata:
        canvas = getattr(getattr(self._window, "_host_api", None), "canvas", None)
        return DocumentMetadata(
            width=int(getattr(canvas, "_width", 1) or 1),
            height=int(getattr(canvas, "_height", 1) or 1),
        )
