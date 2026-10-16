"""Standard multipage print PDF export (CT-11 P4, spec 11 §9/§9.1).

``StandardPdfBackend`` in the spec's terms: the RGB PDF PySide6's
``QPdfWriter`` can produce.  One logical page = one PDF page, logical
reading order (binding never reverses it), crop marks drawn in page
coordinates (never burned into the raster), atomic destination write.

Honest limitations, recorded in the spec: QPdfWriter cannot write
Trim/Bleed/Crop boxes (MediaBox only) and this is **not** PDF/X — the
PdfX backend with real page boxes is Phase P5.  A failed page aborts the
whole export: a partial PDF is never presented as a finished one
(spec 11 §14).  Pages without a physical layout export with the whole
canvas as the trim area and a "no bleed" note (spec 11 §9).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QMarginsF, QPointF, QRectF, QSizeF
from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter, QPen
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from ..core.print_page_geometry import PrintPageGeometry, geometry_for_canvas
from ..io.atomic_export_writer import write_atomically
from ..io.layer_io import DocumentMetadata
from .canvas.page_scene_snapshot import array_to_qimage
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)

__all__ = ["export_print_pdf"]

RenderPage = Callable[[DocumentMetadata, Any], np.ndarray]


def _ts(text: str) -> str:
    return translate_with_fallback("ExportPrintDialog", text)


class _ExportCancelled(Exception):
    pass


class _PageFailed(Exception):
    def __init__(self, page_id: str) -> None:
        super().__init__(page_id)
        self.page_id = page_id


def export_print_pdf(
    parent: QWidget | None,
    page_service: Any,
    document_metadata: DocumentMetadata,
    *,
    page_ids: Sequence[str],
    destination: Path,
    with_marks: bool,
    render_page: RenderPage | None = None,
    progress_factory: Callable[[int], Any] | None = None,
    message_box: Any = QMessageBox,
) -> dict[str, Any]:
    """Returns {"status": "success"|"cancelled"|"failed", "pages": int,
    "warnings": [str, ...]}."""
    persist = getattr(page_service, "persist_active_page", None)
    if callable(persist):
        persist()

    release: Callable[[], None] = _no_release
    if render_page is None:
        render_page, release = _default_render_page_factory()

    if progress_factory is None:

        def progress_factory(total: int) -> Any:
            dialog = QProgressDialog(_ts("Exporting print PDF..."), _ts("Cancel"), 0, total, parent)
            dialog.setMinimumDuration(0)
            dialog.setWindowTitle(_ts("Export for Print"))
            return dialog

    warnings: list[str] = []
    progress = progress_factory(len(page_ids))
    status = "failed"
    try:
        write_atomically(
            Path(destination),
            lambda tmp: _write_pdf(
                tmp,
                page_service,
                document_metadata,
                page_ids,
                with_marks,
                render_page,
                progress,
                warnings,
            ),
        )
        status = "success"
    except _ExportCancelled:
        status = "cancelled"
    except _PageFailed as error:
        logger.warning("print PDF export failed on page %s", error.page_id, exc_info=True)
        message_box.warning(
            parent,
            _ts("Export for Print"),
            _ts("Page {page} could not be rendered; no PDF was written.").format(
                page=error.page_id
            ),
        )
        return {"status": "failed", "pages": 0, "warnings": warnings}
    except Exception:
        logger.warning("print PDF export failed", exc_info=True)
        message_box.warning(parent, _ts("Export for Print"), _ts("The PDF could not be written."))
        return {"status": "failed", "pages": 0, "warnings": warnings}
    finally:
        release()
        close = getattr(progress, "close", None)
        if callable(close):
            close()

    if status == "success":
        summary = _ts("Exported {count} page(s) to {name}.").format(
            count=len(page_ids), name=Path(destination).name
        )
        if warnings:
            summary += "\n" + "\n".join(dict.fromkeys(warnings))
        message_box.information(parent, _ts("Export for Print"), summary)
    return {"status": status, "pages": len(page_ids) if status == "success" else 0}


def _write_pdf(
    tmp_path: Path,
    page_service: Any,
    document_metadata: DocumentMetadata,
    page_ids: Sequence[str],
    with_marks: bool,
    render_page: RenderPage,
    progress: Any,
    warnings: list[str],
) -> None:
    writer = QPdfWriter(str(tmp_path))
    writer.setResolution(72)  # painter units == PDF points
    painter: QPainter | None = None
    try:
        for index, page_id in enumerate(page_ids):
            if progress.wasCanceled():
                raise _ExportCancelled
            progress.setLabelText(str(page_id))
            progress.setValue(index)
            try:
                page = page_service.get_page_snapshot(page_id)
                if page is None:
                    raise ValueError(f"page not found: {page_id}")
                rgba = render_page(document_metadata, page)
                geometry = _page_geometry(document_metadata, page, rgba, with_marks)
            except Exception as error:
                raise _PageFailed(str(page_id)) from error
            if not geometry.has_bleed:
                warnings.append(_ts("Some pages have no bleed; the whole canvas is the trim area."))
            _set_page_size(writer, geometry)
            if painter is None:
                painter = QPainter(writer)
            else:
                writer.newPage()
            _draw_page(painter, geometry, rgba)
            # CT-08 V2b: bound resident pages while the book streams out.
            evict = getattr(page_service, "evict_pages_to_budget", None)
            if callable(evict):
                evict()
        progress.setValue(len(page_ids))
    finally:
        if painter is not None:
            painter.end()


def _page_geometry(
    document_metadata: DocumentMetadata, page: Any, rgba: np.ndarray, with_marks: bool
) -> PrintPageGeometry:
    page_canvas = getattr(page, "canvas", None)
    dpi = tuple(page_canvas.dpi) if page_canvas is not None else tuple(document_metadata.dpi)
    height, width = rgba.shape[:2]
    return geometry_for_canvas(width, height, (float(dpi[0]), float(dpi[1])), with_marks=with_marks)


def _set_page_size(writer: QPdfWriter, geometry: PrintPageGeometry) -> None:
    _x, _y, media_w, media_h = geometry.media_box_pt
    writer.setPageSize(QPageSize(QSizeF(media_w, media_h), QPageSize.Unit.Point))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Point)


def _draw_page(painter: QPainter, geometry: PrintPageGeometry, rgba: np.ndarray) -> None:
    image = array_to_qimage(np.ascontiguousarray(rgba))
    cx, cy, cw, ch = geometry.content_rect_pt
    painter.drawImage(QRectF(cx, cy, cw, ch), image)
    if geometry.marks:
        pen = QPen()
        pen.setWidthF(0.25)
        painter.setPen(pen)
        for mark in geometry.marks:
            painter.drawLine(QPointF(mark.x1, mark.y1), QPointF(mark.x2, mark.y2))


def _default_render_page_factory() -> tuple[RenderPage, Callable[[], None]]:
    from .canvas.page_scene_snapshot import scene_from_page  # noqa: PLC0415
    from .canvas.wgpu_backend import WgpuOffscreenRenderer  # noqa: PLC0415

    renderer = WgpuOffscreenRenderer()

    def render(metadata: DocumentMetadata, page: Any) -> np.ndarray:
        return renderer.render_scene(scene_from_page(metadata, page)).rgba

    def release() -> None:
        renderer.release()

    return render, release


def _no_release() -> None:
    return None
