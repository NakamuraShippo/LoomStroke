"""Atomic PDF/X candidate export with an optional independent verifier."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtWidgets import QProgressDialog, QWidget

from ..core.print_mark_models import PrintMarkOptions
from ..io.atomic_export_writer import write_atomically
from ..io.color_profile_registry import ColorProfileRegistry, RenderingIntent
from ..io.layer_io import DocumentMetadata
from ..io.pdfx_document import PdfXDocument
from .print_pdf_export import _default_render_page_factory, _page_geometry
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)

RenderPage = Callable[[DocumentMetadata, Any], np.ndarray]
VerifyPdfX = Callable[[Path, str], None]


class _ExportCancelled(Exception):
    pass


def export_print_pdfx(
    parent: QWidget | None,
    page_service: Any,
    document_metadata: DocumentMetadata,
    *,
    page_ids: Sequence[str],
    destination: Path,
    conformance: str,
    registry: ColorProfileRegistry,
    source_profile_id: str,
    cmyk_profile_id: str,
    with_marks: bool,
    intent: RenderingIntent = "relative",
    black_point_compensation: bool = True,
    mark_options: PrintMarkOptions | None = None,
    verifier: VerifyPdfX | None = None,
    render_page: RenderPage | None = None,
    progress_factory: Callable[[int], Any] | None = None,
) -> dict[str, Any]:
    """Write a candidate atomically; report verification separately from generation.

    The verifier raises on nonconformance.  A missing verifier returns
    ``pending_validation``; callers must never report that status as PDF/X
    success or silently substitute a standard PDF.
    """
    if not page_ids:
        raise ValueError("PDF/X export requires at least one page")
    persist = getattr(page_service, "persist_active_page", None)
    if callable(persist):
        persist()
    release: Callable[[], None] = _no_release
    if render_page is None:
        render_page, release = _default_render_page_factory()
    if progress_factory is None:

        def progress_factory(total: int) -> QProgressDialog:
            dialog = QProgressDialog(
                translate_with_fallback("ExportPrintDialog", "Exporting print PDF..."),
                translate_with_fallback("ExportPrintDialog", "Cancel"),
                0,
                total,
                parent,
            )
            dialog.setMinimumDuration(0)
            return dialog

    progress = progress_factory(len(page_ids))
    try:
        write_atomically(
            Path(destination),
            lambda temporary: _write_candidate(
                temporary,
                page_service,
                document_metadata,
                page_ids,
                registry,
                source_profile_id,
                cmyk_profile_id,
                conformance,
                with_marks,
                intent,
                black_point_compensation,
                mark_options,
                render_page,
                progress,
                verifier,
            ),
        )
    except _ExportCancelled:
        return {"status": "cancelled", "pages": 0}
    except Exception:
        logger.warning("PDF/X export failed", exc_info=True)
        return {"status": "failed", "pages": 0}
    finally:
        release()
        close = getattr(progress, "close", None)
        if callable(close):
            close()
    return {
        "status": "verified" if verifier is not None else "pending_validation",
        "pages": len(page_ids),
    }


def _write_candidate(
    temporary: Path,
    page_service: Any,
    document_metadata: DocumentMetadata,
    page_ids: Sequence[str],
    registry: ColorProfileRegistry,
    source_profile_id: str,
    cmyk_profile_id: str,
    conformance: str,
    with_marks: bool,
    intent: RenderingIntent,
    black_point_compensation: bool,
    mark_options: PrintMarkOptions | None,
    render_page: RenderPage,
    progress: Any,
    verifier: VerifyPdfX | None,
) -> None:
    document = PdfXDocument(
        registry,
        conformance=conformance,
        cmyk_profile_id=cmyk_profile_id,
        source_profile_id=source_profile_id,
        intent=intent,
        black_point_compensation=black_point_compensation,
    )
    for index, page_id in enumerate(page_ids):
        if progress.wasCanceled():
            raise _ExportCancelled
        progress.setLabelText(str(page_id))
        progress.setValue(index)
        page = page_service.get_page_snapshot(page_id)
        if page is None:
            raise ValueError(f"page not found: {page_id}")
        rgba = render_page(document_metadata, page)
        geometry = _page_geometry(document_metadata, page, rgba, with_marks, mark_options)
        document.add_page(rgba, geometry)
        evict = getattr(page_service, "evict_pages_to_budget", None)
        if callable(evict):
            evict()
    if progress.wasCanceled():
        raise _ExportCancelled
    document.save(temporary)
    if verifier is not None:
        verifier(temporary, conformance)
    progress.setValue(len(page_ids))


def _no_release() -> None:
    return None
