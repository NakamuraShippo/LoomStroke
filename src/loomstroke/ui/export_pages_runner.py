"""Multi-page export execution (CT-10 X2-X4, spec 10 §5 / §12 / §13 / §16).

Runs a validated :class:`ExportPagesPlan` sequentially: page snapshot ->
shared scene conversion -> wgpu offscreen render -> CT-09 raster export
(atomic write).  Existing files follow the plan's collision policy
(``ask`` confirms once with the count, ``replace``, ``skip``, ``suffix``),
cancel stops before the next item and never deletes finished files, a
failing item continues to the next page, per-item warnings (capability
gaps, flattened alpha) are kept on the :class:`BatchExportResult` and
summarised, and an optional ``export-report.json`` lands next to the files.

Known limitation, recorded in the spec: each page still renders on the UI
thread; the CT-08 page-lease render/encode pipeline is Phase X3.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from ..core.batch_export_item_result import BatchExportItemResult
from ..core.batch_export_result import BatchExportResult
from ..core.export_collision_policy import CollisionDecision, ExportCollisionPolicy
from ..core.export_report_writer import ExportReportWriter
from ..io.export_contract import ExportIssue
from ..io.layer_io import DocumentMetadata
from ..io.raster_export import export_raster
from .dialogs.export_pages_dialog import ExportPagesPlan
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)

__all__ = ["run_export_pages"]

RenderPage = Callable[[DocumentMetadata, Any], np.ndarray]
_SUMMARY_ISSUE_LIMIT = 4


def _ts(text: str) -> str:
    return translate_with_fallback("ExportPagesDialog", text)


def _default_render_page_factory() -> tuple[RenderPage, Callable[[], None]]:
    """(render, release) over one shared offscreen renderer."""
    from .canvas.page_scene_snapshot import scene_from_page  # noqa: PLC0415
    from .canvas.wgpu_backend import WgpuOffscreenRenderer  # noqa: PLC0415

    renderer = WgpuOffscreenRenderer()

    def render(metadata: DocumentMetadata, page: Any) -> np.ndarray:
        return renderer.render_scene(scene_from_page(metadata, page)).rgba

    def release() -> None:
        renderer.release()

    return render, release


def run_export_pages(
    parent: QWidget | None,
    page_service: Any,
    document_metadata: DocumentMetadata,
    plan: ExportPagesPlan,
    *,
    render_page: RenderPage | None = None,
    progress_factory: Callable[[int], Any] | None = None,
    message_box: Any = QMessageBox,
    document_name: str = "",
) -> dict[str, Any]:
    """Execute the plan.

    Returns ``{"exported", "failed", "skipped", "warnings": [file names],
    "result": BatchExportResult, "report": Path | None}``; ``skipped``
    covers both collision skips and items cancelled before they started.
    """
    # The active page's stored snapshot only reflects the canvas as of the
    # last persist (page switch / save), so exporting it without this step
    # drops edits made since — e.g. an effect layer added on the live canvas.
    persist_active = getattr(page_service, "persist_active_page", None)
    if callable(persist_active):
        persist_active()
    destinations = [plan.destination / name for name in plan.file_names]
    policy = ExportCollisionPolicy.normalize(getattr(plan, "collision_policy", "ask"))
    decisions = ExportCollisionPolicy().resolve(destinations, policy)
    if policy == "ask":
        existing = [d.destination.name for d in decisions if d.existed and d.destination]
        if existing:
            answer = message_box.question(
                parent,
                _ts("Export Pages"),
                _ts("{count} file(s) already exist and will be replaced. Continue?").format(
                    count=len(existing)
                ),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return _as_dict(
                    BatchExportResult(
                        tuple(
                            BatchExportItemResult(page_id, destination, "cancelled")
                            for page_id, destination in zip(
                                plan.page_ids, destinations, strict=True
                            )
                        )
                    ),
                    None,
                )

    def _no_release() -> None:
        return None

    release: Callable[[], None] = _no_release
    if render_page is None:
        render_page, release = _default_render_page_factory()

    if progress_factory is None:

        def progress_factory(total: int) -> Any:
            dialog = QProgressDialog(_ts("Exporting pages..."), _ts("Cancel"), 0, total, parent)
            dialog.setMinimumDuration(0)
            dialog.setWindowTitle(_ts("Export Pages"))
            return dialog

    progress = progress_factory(len(plan.page_ids))
    try:
        result = _export_items(
            page_service,
            document_metadata,
            plan,
            destinations,
            decisions,
            render_page,
            progress,
        )
        progress.setValue(len(plan.page_ids))
    finally:
        release()
        close = getattr(progress, "close", None)
        if callable(close):
            close()

    report_path: Path | None = None
    report_failed = False
    if getattr(plan, "write_report", False):
        try:
            report_path = ExportReportWriter().write(
                plan.destination,
                result,
                format_id=plan.format_id,
                document_name=document_name,
            )
        except Exception:
            logger.warning("export report could not be written", exc_info=True)
            report_failed = True

    _show_summary(parent, plan, result, message_box, report_failed=report_failed)
    return _as_dict(result, report_path)


def _export_items(
    page_service: Any,
    document_metadata: DocumentMetadata,
    plan: ExportPagesPlan,
    planned: list[Path],
    decisions: list[CollisionDecision],
    render_page: RenderPage,
    progress: Any,
) -> BatchExportResult:
    items: list[BatchExportItemResult] = []
    rows = zip(plan.page_ids, planned, decisions, strict=True)
    for index, (page_id, planned_path, decision) in enumerate(rows):
        destination = decision.destination
        if destination is None:
            # Collision policy "skip": the existing file stays untouched and
            # the item keeps its planned name so the summary/report can list it.
            items.append(BatchExportItemResult(page_id, planned_path, "skipped"))
            continue
        if progress.wasCanceled():
            items.append(BatchExportItemResult(page_id, destination, "cancelled"))
            continue
        progress.setLabelText(destination.name)
        progress.setValue(index)
        items.append(
            _export_item(page_service, document_metadata, plan, page_id, destination, render_page)
        )
        # CT-08 V2b: pages hydrated just for this export go back to
        # placeholders as the loop advances, keeping memory bounded.
        evict = getattr(page_service, "evict_pages_to_budget", None)
        if callable(evict):
            evict()
    return BatchExportResult(tuple(items))


def _export_item(
    page_service: Any,
    document_metadata: DocumentMetadata,
    plan: ExportPagesPlan,
    page_id: str,
    destination: Path,
    render_page: RenderPage,
) -> BatchExportItemResult:
    try:
        page = page_service.get_page_snapshot(page_id)
        if page is None:
            raise ValueError(f"page not found: {page_id}")
        rgba = render_page(document_metadata, page)
        page_canvas = getattr(page, "canvas", None)
        dpi = tuple(page_canvas.dpi) if page_canvas is not None else None
        result = export_raster(
            np.ascontiguousarray(rgba),
            destination,
            plan.format_id,
            canvas_dpi=dpi,
            document_dpi=tuple(document_metadata.dpi),
            background_rgba=tuple(document_metadata.background_color),
        )
    except Exception as error:
        logger.warning("export failed for page %s", page_id, exc_info=True)
        return BatchExportItemResult(
            page_id,
            destination,
            "failed",
            (ExportIssue(code="export_failed", message=str(error), severity="error"),),
        )
    if result.status == "failed":
        return BatchExportItemResult(page_id, destination, "failed", tuple(result.issues))
    status = "warning" if result.issues else "success"
    return BatchExportItemResult(page_id, destination, status, tuple(result.issues))


def _as_dict(result: BatchExportResult, report: Path | None) -> dict[str, Any]:
    return {
        "exported": result.exported,
        "failed": result.failed,
        "skipped": result.skipped,
        "warnings": result.warnings,
        "result": result,
        "report": report,
    }


def _show_summary(
    parent: QWidget | None,
    plan: ExportPagesPlan,
    result: BatchExportResult,
    message_box: Any,
    *,
    report_failed: bool = False,
) -> None:
    summary = _ts("Exported {done} of {total} page(s).").format(
        done=len(result.exported), total=len(plan.page_ids)
    )
    if result.skipped:
        summary += "\n" + _ts("Skipped: {count}").format(count=len(result.skipped))
    codes = result.issue_codes()
    if codes:
        shown = ", ".join(
            f"{code} x{count}" for code, count in list(sorted(codes.items()))[:_SUMMARY_ISSUE_LIMIT]
        )
        summary += "\n" + _ts("Warnings: {codes}").format(codes=shown)
    if report_failed:
        summary += "\n" + _ts("The export report could not be written.")
    if result.failed:
        summary += "\n" + _ts("Failed: {names}").format(names=", ".join(result.failed))
        message_box.warning(parent, _ts("Export Pages"), summary)
    else:
        message_box.information(parent, _ts("Export Pages"), summary)
