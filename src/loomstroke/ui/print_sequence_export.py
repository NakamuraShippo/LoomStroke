"""CMYK TIFF / Grayscale-K sequence export (CT-11 P3, spec 11 §5.2 / §9.2 / §14).

One logical page -> one flattened TIFF written by a
:class:`PrintPageConverter`, in logical order.  Collision policy, cancel
and per-item results follow the CT-10 Export Pages contract (the same
:class:`ExportCollisionPolicy` / :class:`BatchExportResult` types): a
failing page is recorded and the sequence continues, cancel stops before
the next page and never deletes finished files, existing files are only
replaced after one explicit confirmation (``ask``).

Known limitation (shared with CT-10 X3): pages render on the UI thread.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from ..core.batch_export_item_result import BatchExportItemResult
from ..core.batch_export_result import BatchExportResult
from ..core.export_collision_policy import CollisionDecision, ExportCollisionPolicy
from ..io.export_contract import ExportIssue
from ..io.layer_io import DocumentMetadata
from ..io.print_page_converter import PrintPageConverter
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)

__all__ = ["export_print_sequence"]

RenderPage = Callable[[DocumentMetadata, Any], np.ndarray]


def _ts(text: str) -> str:
    return translate_with_fallback("ExportPrintDialog", text)


def export_print_sequence(
    parent: QWidget | None,
    page_service: Any,
    document_metadata: DocumentMetadata,
    *,
    page_ids: Sequence[str],
    destinations: Sequence[Path],
    converter: PrintPageConverter,
    collision_policy: str = "ask",
    render_page: RenderPage | None = None,
    progress_factory: Callable[[int], Any] | None = None,
    message_box: Any = QMessageBox,
) -> dict[str, Any]:
    """Write one TIFF per page.  Returns ``{"exported", "failed", "skipped",
    "warnings": [file names], "result": BatchExportResult}``."""
    if len(page_ids) != len(destinations):
        raise ValueError("page_ids and destinations must have the same length")
    persist = getattr(page_service, "persist_active_page", None)
    if callable(persist):
        persist()
    planned = [Path(destination) for destination in destinations]
    policy = ExportCollisionPolicy.normalize(collision_policy)
    decisions = ExportCollisionPolicy().resolve(planned, policy)
    if policy == "ask":
        existing = [d.destination.name for d in decisions if d.existed and d.destination]
        if existing:
            answer = message_box.question(
                parent,
                _ts("Export for Print"),
                _ts("{count} file(s) already exist and will be replaced. Continue?").format(
                    count=len(existing)
                ),
            )
            if answer != QMessageBox.StandardButton.Yes:
                cancelled = tuple(
                    BatchExportItemResult(page_id, destination, "cancelled")
                    for page_id, destination in zip(page_ids, planned, strict=True)
                )
                return _as_dict(BatchExportResult(cancelled))

    release: Callable[[], None] = _no_release
    if render_page is None:
        render_page, release = _default_render_page_factory()
    if progress_factory is None:

        def progress_factory(total: int) -> Any:
            dialog = QProgressDialog(
                _ts("Exporting print pages..."), _ts("Cancel"), 0, total, parent
            )
            dialog.setMinimumDuration(0)
            dialog.setWindowTitle(_ts("Export for Print"))
            return dialog

    progress = progress_factory(len(page_ids))
    try:
        result = _export_items(
            page_service,
            document_metadata,
            list(page_ids),
            planned,
            decisions,
            converter,
            render_page,
            progress,
        )
        progress.setValue(len(page_ids))
    finally:
        release()
        close = getattr(progress, "close", None)
        if callable(close):
            close()
    _show_summary(parent, len(page_ids), result, message_box)
    return _as_dict(result)


def _export_items(
    page_service: Any,
    document_metadata: DocumentMetadata,
    page_ids: list[str],
    planned: list[Path],
    decisions: list[CollisionDecision],
    converter: PrintPageConverter,
    render_page: RenderPage,
    progress: Any,
) -> BatchExportResult:
    items: list[BatchExportItemResult] = []
    rows = zip(page_ids, planned, decisions, strict=True)
    for index, (page_id, planned_path, decision) in enumerate(rows):
        destination = decision.destination
        if destination is None:
            items.append(BatchExportItemResult(page_id, planned_path, "skipped"))
            continue
        if progress.wasCanceled():
            items.append(BatchExportItemResult(page_id, destination, "cancelled"))
            continue
        progress.setLabelText(destination.name)
        progress.setValue(index)
        items.append(
            _export_item(
                page_service, document_metadata, page_id, destination, converter, render_page
            )
        )
        evict = getattr(page_service, "evict_pages_to_budget", None)
        if callable(evict):
            evict()
    return BatchExportResult(tuple(items))


def _export_item(
    page_service: Any,
    document_metadata: DocumentMetadata,
    page_id: str,
    destination: Path,
    converter: PrintPageConverter,
    render_page: RenderPage,
) -> BatchExportItemResult:
    try:
        page = page_service.get_page_snapshot(page_id)
        if page is None:
            raise ValueError(f"page not found: {page_id}")
        page_canvas = getattr(page, "canvas", None)
        dpi = tuple(page_canvas.dpi) if page_canvas is not None else tuple(document_metadata.dpi)
        _write_page_output(document_metadata, page, converter, render_page, destination, dpi)
    except Exception as error:
        logger.warning("print sequence export failed for page %s", page_id, exc_info=True)
        return BatchExportItemResult(
            page_id,
            destination,
            "failed",
            (ExportIssue(code="export_failed", message=str(error), severity="error"),),
        )
    return BatchExportItemResult(page_id, destination, "success")


def _write_page_output(metadata, page, converter, render_page, destination, dpi):
    from .canvas.native_cmyk_scene_renderer import NativeCmykSceneRenderer  # noqa: PLC0415
    from .canvas.page_scene_snapshot import scene_from_page  # noqa: PLC0415

    pending = list(getattr(page, "layers", ()))
    native = False
    while pending:
        layer = pending.pop()
        native = native or getattr(layer, "native_cmyk", None) is not None
        pending.extend(getattr(layer, "children", ()))
    if getattr(converter, "output", None) == "cmyk_tiff" and native:
        scene = scene_from_page(metadata, page)
        renderer = NativeCmykSceneRenderer()
        try:
            converter.write_native_page(renderer.render(scene), destination, dpi=dpi)
        finally:
            renderer.release()
    else:
        converter.write_page(render_page(metadata, page), destination, dpi=dpi)


def _as_dict(result: BatchExportResult) -> dict[str, Any]:
    return {
        "exported": result.exported,
        "failed": result.failed,
        "skipped": result.skipped,
        "warnings": result.warnings,
        "result": result,
    }


def _show_summary(
    parent: QWidget | None, total: int, result: BatchExportResult, message_box: Any
) -> None:
    summary = _ts("Exported {done} of {total} page(s).").format(
        done=len(result.exported), total=total
    )
    if result.skipped:
        summary += "\n" + _ts("Skipped: {count}").format(count=len(result.skipped))
    if result.failed:
        summary += "\n" + _ts("Failed: {names}").format(names=", ".join(result.failed))
        message_box.warning(parent, _ts("Export for Print"), summary)
    else:
        message_box.information(parent, _ts("Export for Print"), summary)


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
