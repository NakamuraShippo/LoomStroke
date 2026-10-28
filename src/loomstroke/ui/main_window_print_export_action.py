"""``File > Export for Print...`` action for MainWindow (CT-11 P3/P4, spec 11 §5.2).

Dialog (scope / output kind / colour / names / geometry preview) ->
destination chooser (a PDF file, or a folder for TIFF sequences) ->
:func:`export_print_pdf` or :func:`export_print_sequence`.  The action
owns the session's print :class:`ColorProfileRegistry`, so a CMYK profile
loaded once stays available for later exports; the document's working
ICC profile (CT-09) is the source, sRGB is assumed when there is none.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from ..core.export_naming import NamingContext, NamingError, detect_collisions, render_export_names
from ..io.bundled_color_profiles import create_print_profile_registry
from ..io.color_profile_registry import ColorProfileRegistry
from ..io.layer_io import DocumentMetadata
from ..io.print_page_converter import PrintPageConverter
from .dialogs.export_print_dialog import ExportPrintContext, ExportPrintDialog, ExportPrintPlan
from .preflight_export_gate import PreflightExportGate
from .print_pdf_export import export_print_pdf
from .print_sequence_export import export_print_sequence
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)

__all__ = ["MainWindowPrintExportAction"]


def _ts(text: str) -> str:
    return translate_with_fallback("ExportPrintDialog", text)


class MainWindowPrintExportAction:
    """Run the Export for Print dialog and write the chosen output."""

    def __init__(
        self,
        window: Any,
        *,
        document_metadata: Callable[[], DocumentMetadata] | None = None,
        registry: ColorProfileRegistry | None = None,
        dialog_factory: Callable[..., Any] | None = None,
        file_dialog: Callable[[], Any] | None = None,
        dialog_type: Callable[[], Any] | None = None,
        message_box: Callable[[], Any] | None = None,
        pdf_exporter: Callable[..., Any] | None = None,
        sequence_exporter: Callable[..., Any] | None = None,
        export_gate: Any | None = None,
    ) -> None:
        self._window = window
        self._document_metadata = document_metadata or self._fallback_document_metadata
        self._registry = registry if registry is not None else create_print_profile_registry()
        self._dialog_factory = dialog_factory or ExportPrintDialog
        self._file_dialog = file_dialog or (lambda: QFileDialog)
        self._dialog_type = dialog_type or (lambda: QDialog)
        self._message_box = message_box or (lambda: QMessageBox)
        self._pdf_exporter = pdf_exporter or export_print_pdf
        self._sequence_exporter = sequence_exporter or export_print_sequence
        self._export_gate = export_gate or PreflightExportGate(
            window, document_metadata=self._document_metadata
        )

    @property
    def registry(self) -> ColorProfileRegistry:
        return self._registry

    def run(self) -> bool:
        """Return ``True`` when an export was started."""
        window = self._window
        pages = getattr(getattr(window, "_host_api", None), "pages", None)
        list_pages = getattr(pages, "list_pages", None)
        snapshot = getattr(pages, "get_page_snapshot", None)
        summaries = list(list_pages() or []) if callable(list_pages) else []
        if not summaries or not callable(snapshot):
            self._warn("No pages are available to export.")
            return False
        page_ids = tuple(str(summary.id) for summary in summaries)
        names_by_id = {
            str(summary.id): str(getattr(summary, "name", "") or summary.id)
            for summary in summaries
        }
        context = ExportPrintContext(
            page_ids=page_ids,
            metadata_by_id=self._page_metadata_by_id(pages, snapshot, page_ids),
            current_page_id=getattr(pages, "get_active_page_id", lambda: None)(),
            canvas_size_px=self._canvas_size(),
            dpi=tuple(self._document_metadata().dpi),
            document_profile_name=self._document_profile_name(),
        )
        dialog = self._dialog_factory(context=context, registry=self._registry, parent=window)
        if dialog.exec() != self._dialog_type().DialogCode.Accepted:
            return False
        plan = dialog.export_plan()
        if plan is None:
            return False
        # CT-16: fatal preflight issues need one explicit confirmation.
        if not self._export_gate.allow("Export for Print"):
            return False
        if plan.is_sequence:
            return self._export_sequence(pages, plan, names_by_id)
        return self._export_pdf(pages, plan)

    # -- PDF ------------------------------------------------------------------------

    def _export_pdf(self, pages: Any, plan: ExportPrintPlan) -> bool:
        window = self._window
        document_path = getattr(window, "_document_path", None)
        default = Path(document_path).with_suffix(".pdf") if document_path else Path("untitled.pdf")
        path_text, _selected = self._file_dialog().getSaveFileName(
            window,
            _ts("Export for Print"),
            str(default),
            _ts("PDF Document (*.pdf)"),
        )
        if not path_text:
            return False
        self._pdf_exporter(
            window,
            pages,
            self._document_metadata(),
            page_ids=plan.page_ids,
            destination=Path(path_text),
            with_marks=plan.with_marks,
        )
        return True

    # -- TIFF sequences -------------------------------------------------------------

    def _export_sequence(
        self, pages: Any, plan: ExportPrintPlan, names_by_id: dict[str, str]
    ) -> bool:
        window = self._window
        source_id = self._source_profile_id()
        if source_id is None:
            return False
        try:
            converter = PrintPageConverter(
                self._registry,
                output=plan.output,
                source_profile_id=source_id,
                cmyk_profile_id=plan.cmyk_profile_id,
                intent=plan.intent,  # type: ignore[arg-type]
                black_point_compensation=plan.black_point_compensation,
            )
        except ValueError as error:
            self._warn_formatted("The output profile could not be used: {error}", error=error)
            return False
        try:
            file_names = self._file_names(plan, names_by_id)
        except NamingError as error:
            self._warn_formatted("Invalid file name template: {error}", error=error)
            return False
        collisions = detect_collisions(file_names)
        if collisions:
            self._message_box().warning(
                window,
                _ts("Export for Print"),
                translate_with_fallback(
                    "ExportPagesDialog",
                    "The template produces duplicate file names (e.g. {name}).",
                ).format(name=collisions[0]),
            )
            return False
        document_path = getattr(window, "_document_path", None)
        start = str(Path(document_path).parent) if document_path else ""
        directory = self._file_dialog().getExistingDirectory(
            window, _ts("Choose Output Folder"), start
        )
        if not directory:
            return False
        destinations = [Path(directory) / name for name in file_names]
        self._sequence_exporter(
            window,
            pages,
            self._document_metadata(),
            page_ids=plan.page_ids,
            destinations=destinations,
            converter=converter,
            collision_policy=plan.collision_policy,
        )
        return True

    def _file_names(self, plan: ExportPrintPlan, names_by_id: dict[str, str]) -> list[str]:
        document_path = getattr(self._window, "_document_path", None)
        document_name = Path(document_path).stem if document_path else "untitled"
        contexts = [
            NamingContext(
                index=index + 1,
                page_id=page_id,
                page_name=names_by_id.get(page_id, page_id),
                folio=None,
                role="body",
                document_name=document_name,
            )
            for index, page_id in enumerate(plan.page_ids)
        ]
        zero_pad = max(3, len(str(len(plan.page_ids))))
        return list(
            render_export_names(
                plan.naming_template,
                contexts,
                zero_pad=zero_pad,
                extension=PrintPageConverter.EXTENSION,
            )
        )

    # -- helpers --------------------------------------------------------------------

    def _canvas(self) -> Any | None:
        host_api = getattr(self._window, "_host_api", None)
        return getattr(host_api, "canvas", None) if host_api else None

    def _canvas_size(self) -> tuple[int, int]:
        metadata = self._document_metadata()
        return (int(metadata.width), int(metadata.height))

    def _fallback_document_metadata(self) -> DocumentMetadata:
        canvas = self._canvas()
        return DocumentMetadata(
            width=int(getattr(canvas, "_width", 1) or 1),
            height=int(getattr(canvas, "_height", 1) or 1),
        )

    def _document_profile(self) -> Any | None:
        profile = getattr(self._canvas(), "color_profile", None)
        if profile is None or not getattr(profile, "data", None):
            return None
        return profile

    def _document_profile_name(self) -> str | None:
        profile = self._document_profile()
        if profile is None:
            return None
        try:
            return self._registry.register(bytes(profile.data)).name
        except ValueError:
            return getattr(profile, "name", None)

    def _source_profile_id(self) -> str | None:
        """Document ICC (validated through the registry) or built-in sRGB."""
        profile = self._document_profile()
        if profile is None:
            return self._registry.srgb().profile_id
        try:
            return self._registry.register(bytes(profile.data)).profile_id
        except ValueError:
            # spec 11 §14: never fall back to sRGB silently for a tagged document.
            self._warn("The document profile is not a usable ICC profile.")
            return None

    @staticmethod
    def _page_metadata_by_id(pages: Any, snapshot: Any, page_ids: Any) -> dict[str, Any]:
        reader = getattr(pages, "get_page_metadata", None)
        if callable(reader):
            return {page_id: reader(page_id) for page_id in page_ids}
        return {page_id: getattr(snapshot(page_id), "metadata", None) for page_id in page_ids}

    def _warn(self, text: str) -> None:
        window = self._window
        self._message_box().warning(window, _ts("Export for Print"), _ts(text))

    def _warn_formatted(self, text: str, **values: Any) -> None:
        window = self._window
        self._message_box().warning(window, _ts("Export for Print"), _ts(text).format(**values))
