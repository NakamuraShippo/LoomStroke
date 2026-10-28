"""Webtoon package export execution (CT-12 W2, spec 12 §6.2 / §10 / §15).

Plan -> stream (resolver) -> slices (fixed-height slicer) -> names (CT-10
allowlist parser) -> collision policy -> streamed compose/encode through
the :class:`WebtoonExportCoordinator` -> optional manifest -> summary.
Pages render on demand through the shared offscreen path and are evicted
back to the CT-08 budget after each fetch; only one scaled page or two
and one slice buffer are alive at any time.
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
from ..core.export_collision_policy import ExportCollisionPolicy
from ..core.export_naming import NamingContext, detect_collisions, render_export_names
from ..core.webtoon.fixed_height_slicer import FixedHeightSlicer
from ..core.webtoon.stream_stripe_composer import StreamStripeComposer
from ..core.webtoon.vertical_stream_resolver import VerticalStreamResolver
from ..core.webtoon.webtoon_export_coordinator import WebtoonExportCoordinator
from ..core.webtoon.webtoon_manifest_writer import WebtoonManifestWriter
from ..io.export_contract import ExportIssue
from ..io.layer_io import DocumentMetadata
from .dialogs.export_webtoon_dialog import ExportWebtoonPlan
from .offscreen_page_renderer import OffscreenPageRenderer
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)

__all__ = ["run_webtoon_export"]

RenderPage = Callable[[DocumentMetadata, Any], np.ndarray]
_EXTENSIONS = {"png": "png", "jpeg": "jpg", "webp": "webp"}


def _ts(text: str) -> str:
    return translate_with_fallback("ExportWebtoonDialog", text)


def run_webtoon_export(
    parent: QWidget | None,
    page_service: Any,
    document_metadata: DocumentMetadata,
    plan: ExportWebtoonPlan,
    *,
    destination_dir: Path,
    render_page: RenderPage | None = None,
    progress_factory: Callable[[int], Any] | None = None,
    message_box: Any = QMessageBox,
) -> dict[str, Any]:
    """Returns ``{"exported", "failed", "skipped", "warnings", "result",
    "artifacts", "manifest", "stream", "slices"}``."""
    persist = getattr(page_service, "persist_active_page", None)
    if callable(persist):
        persist()
    stream = VerticalStreamResolver().build(plan.segments, target_width=plan.target_width_px)
    slices = FixedHeightSlicer().plan(
        stream,
        max_height=plan.max_slice_height_px,
        min_height=plan.profile.min_slice_height_px,
    )
    file_names = _file_names(plan, slices, stream)
    planned = [Path(destination_dir) / name for name in file_names]
    collisions = detect_collisions(file_names)
    if collisions:
        message = translate_with_fallback(
            "ExportPagesDialog",
            "The template produces duplicate file names (e.g. {name}).",
        ).format(name=collisions[0])
        issue = ExportIssue(code="export_failed", message=message, severity="error")
        failed = BatchExportResult(
            tuple(
                BatchExportItemResult(f"slice_{s.index + 1}", d, "failed", (issue,))
                for s, d in zip(slices, planned, strict=True)
            )
        )
        message_box.warning(parent, _ts("Export Webtoon"), message)
        return _as_dict(failed, (), None, stream, slices)
    destinations = _resolve_destinations(parent, plan.collision_policy, planned, message_box)
    if destinations is None:
        cancelled = BatchExportResult(
            tuple(
                BatchExportItemResult(f"slice_{s.index + 1}", d, "cancelled")
                for s, d in zip(slices, planned, strict=True)
            )
        )
        return _as_dict(cancelled, (), None, stream, slices)

    offscreen: OffscreenPageRenderer | None = None
    if render_page is None:
        offscreen = OffscreenPageRenderer()
        render_page = offscreen.render
    page_rgba = _page_rgba_reader(page_service, document_metadata, render_page)
    progress = (progress_factory or _default_progress_factory(parent))(len(slices))

    def should_continue(index: int, total: int) -> bool:
        del total
        if progress.wasCanceled():
            return False
        progress.setLabelText(file_names[index] if index < len(file_names) else "")
        progress.setValue(index)
        return True

    composer = StreamStripeComposer(stream, page_rgba=page_rgba)
    try:
        result, artifacts = WebtoonExportCoordinator(composer).export(
            slices,
            destinations=destinations,
            planned=planned,
            format_id=plan.format_id,
            dpi=tuple(document_metadata.dpi),
            background_rgba=tuple(document_metadata.background_color),
            max_file_bytes=plan.profile.max_file_bytes,
            should_continue=should_continue,
        )
        progress.setValue(len(slices))
    finally:
        if offscreen is not None:
            offscreen.release()
        close = getattr(progress, "close", None)
        if callable(close):
            close()

    manifest, manifest_failed = _write_manifest(
        plan, Path(destination_dir), stream, artifacts, result
    )
    _show_summary(parent, len(slices), result, message_box, manifest_failed=manifest_failed)
    return _as_dict(result, artifacts, manifest, stream, slices)


def _resolve_destinations(
    parent: QWidget | None, collision_policy: str, planned: list[Path], message_box: Any
) -> list[Path | None] | None:
    """Apply the collision policy; ``None`` when the user declines to replace."""
    policy = ExportCollisionPolicy.normalize(collision_policy)
    decisions = ExportCollisionPolicy().resolve(planned, policy)
    if policy == "ask":
        existing = [d.destination.name for d in decisions if d.existed and d.destination]
        if existing:
            answer = message_box.question(
                parent,
                _ts("Export Webtoon"),
                _ts("{count} file(s) already exist and will be replaced. Continue?").format(
                    count=len(existing)
                ),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
    return [decision.destination for decision in decisions]


def _page_rgba_reader(
    page_service: Any, document_metadata: DocumentMetadata, render_page: RenderPage
) -> Callable[[str], np.ndarray]:
    def page_rgba(page_id: str) -> np.ndarray:
        page = page_service.get_page_snapshot(page_id)
        if page is None:
            raise ValueError(f"page not found: {page_id}")
        rgba = np.ascontiguousarray(render_page(document_metadata, page))
        # CT-08 V2b: pages hydrated for this export go back to placeholders.
        evict = getattr(page_service, "evict_pages_to_budget", None)
        if callable(evict):
            evict()
        return rgba

    return page_rgba


def _default_progress_factory(parent: QWidget | None) -> Callable[[int], Any]:
    def factory(total: int) -> Any:
        dialog = QProgressDialog(
            _ts("Exporting webtoon slices..."), _ts("Cancel"), 0, total, parent
        )
        dialog.setMinimumDuration(0)
        dialog.setWindowTitle(_ts("Export Webtoon"))
        return dialog

    return factory


def _write_manifest(
    plan: ExportWebtoonPlan, directory: Path, stream: Any, artifacts: Any, result: Any
) -> tuple[Path | None, bool]:
    if not plan.write_manifest:
        return None, False
    try:
        return (
            WebtoonManifestWriter().write(
                directory,
                profile=plan.profile,
                stream=stream,
                artifacts=artifacts,
                result=result,
                format_id=plan.format_id,
                episode_name=plan.episode_name,
            ),
            False,
        )
    except Exception:
        logger.warning("webtoon manifest could not be written", exc_info=True)
        return None, True


def _file_names(plan: ExportWebtoonPlan, slices: Any, stream: Any) -> list[str]:
    contexts = []
    for plan_slice in slices:
        spans = stream.spans_between(plan_slice.stream_y0, plan_slice.stream_y1)
        first_page = spans[0].page_id if spans else f"slice_{plan_slice.index + 1}"
        contexts.append(
            NamingContext(
                index=plan_slice.index + 1,
                page_id=first_page,
                page_name=first_page,
                folio=None,
                role="body",
                document_name=plan.episode_name or "untitled",
            )
        )
    zero_pad = max(3, len(str(len(slices))))
    extension = _EXTENSIONS.get(plan.format_id, plan.format_id)
    return list(
        render_export_names(plan.naming_template, contexts, zero_pad=zero_pad, extension=extension)
    )


def _as_dict(
    result: BatchExportResult, artifacts: Any, manifest: Path | None, stream: Any, slices: Any
) -> dict[str, Any]:
    return {
        "exported": result.exported,
        "failed": result.failed,
        "skipped": result.skipped,
        "warnings": result.warnings,
        "result": result,
        "artifacts": tuple(artifacts),
        "manifest": manifest,
        "stream": stream,
        "slices": tuple(slices),
    }


def _show_summary(
    parent: QWidget | None,
    total: int,
    result: BatchExportResult,
    message_box: Any,
    *,
    manifest_failed: bool,
) -> None:
    summary = _ts("Exported {done} of {total} slice(s).").format(
        done=len(result.exported), total=total
    )
    if result.skipped:
        summary += "\n" + _ts("Skipped: {count}").format(count=len(result.skipped))
    codes = result.issue_codes()
    if codes:
        shown = ", ".join(f"{code} x{count}" for code, count in sorted(codes.items())[:4])
        summary += "\n" + _ts("Warnings: {codes}").format(codes=shown)
    if manifest_failed:
        summary += "\n" + _ts("The manifest could not be written.")
    if result.failed:
        summary += "\n" + _ts("Failed: {names}").format(names=", ".join(result.failed))
        message_box.warning(parent, _ts("Export Webtoon"), summary)
    else:
        message_box.information(parent, _ts("Export Webtoon"), summary)
