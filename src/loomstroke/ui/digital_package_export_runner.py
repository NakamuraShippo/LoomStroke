"""Digital comic package export execution (CT-26 spec §Package validation / §性能).

Plan -> publication items (planner) -> pre-write validation -> pages
rendered one at a time through the shared offscreen path and encoded
with the CT-09 raster exporter into a sibling staging folder (hash /
size / dimensions recorded per item) -> format writer into staging ->
reopen + validate -> atomic move to the destination -> optional EPUBCheck
-> summary.  Cancel, validation failure or an exception leave the
existing destination untouched and remove the staging folder.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from ..core.digital_package.cbz_package_writer import CbzPackageWriter
from ..core.digital_package.digital_package_request import DigitalComicPackageRequest
from ..core.digital_package.digital_package_result import DigitalPackageResult
from ..core.digital_package.epub_check_adapter import EpubCheckAdapter
from ..core.digital_package.fixed_layout_epub_writer import FixedLayoutEpubWriter
from ..core.digital_package.image_manifest_package_writer import ImageManifestPackageWriter
from ..core.digital_package.package_issue import PackageIssue
from ..core.digital_package.publication_item import PublicationItem
from ..core.digital_package.publication_package_validator import PublicationPackageValidator
from ..core.digital_package.publication_planner import DigitalComicPackagePlanner
from ..io.layer_io import DocumentMetadata
from ..io.raster_export import export_raster
from .dialogs.export_digital_package_dialog import ExportDigitalPackagePlan
from .offscreen_page_renderer import OffscreenPageRenderer
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)

__all__ = ["run_digital_package_export"]

RenderPage = Callable[[DocumentMetadata, Any], np.ndarray]
_EXTENSIONS = {"png": "png", "jpeg": "jpg", "webp": "webp"}


class _Cancelled(Exception):
    pass


def _ts(text: str) -> str:
    return translate_with_fallback("ExportDigitalPackageDialog", text)


def run_digital_package_export(
    parent: QWidget | None,
    page_service: Any,
    document_metadata: DocumentMetadata,
    plan: ExportDigitalPackagePlan,
    *,
    destination: Path,
    render_page: RenderPage | None = None,
    progress_factory: Callable[[int], Any] | None = None,
    message_box: Any = QMessageBox,
    epub_check: EpubCheckAdapter | None = None,
) -> DigitalPackageResult:
    persist = getattr(page_service, "persist_active_page", None)
    if callable(persist):
        persist()
    destination = Path(destination)
    extension = _EXTENSIONS.get(plan.image_format, plan.image_format)
    items = DigitalComicPackagePlanner().plan(
        plan.page_ids,
        roles_by_id=plan.roles_by_id,
        spread_sides_by_id=plan.spread_sides_by_id,  # type: ignore[arg-type]
        extension=extension,
        cover_page_id=plan.cover_page_id,
    )
    validator = PublicationPackageValidator()
    pre_issues = validator.validate_items(items)
    if any(issue.severity == "fatal" for issue in pre_issues):
        result = DigitalPackageResult("failed", plan.format_id, None, issues=pre_issues)
        _show_summary(parent, result, message_box)
        return result
    if not _confirm_replace(parent, destination, plan.format_id, message_box):
        return DigitalPackageResult("cancelled", plan.format_id, None, issues=pre_issues)

    offscreen: OffscreenPageRenderer | None = None
    if render_page is None:
        offscreen = OffscreenPageRenderer()
        render_page = offscreen.render
    progress = (progress_factory or _default_progress_factory(parent))(len(items) + 1)
    staging = _staging_dir(destination)
    result: DigitalPackageResult
    try:
        encoded = _encode_items(
            page_service, document_metadata, plan, items, staging, render_page, progress
        )
        request = DigitalComicPackageRequest(
            format_id=plan.format_id,  # type: ignore[arg-type]
            binding=plan.binding,  # type: ignore[arg-type]
            items=encoded,
            metadata=plan.metadata,
            destination=destination,
            include_loomstroke_manifest=plan.include_loomstroke_manifest,
        )
        progress.setLabelText(destination.name)
        progress.setValue(len(items))
        result = _write_and_validate(request, staging, validator, epub_check, pre_issues)
        progress.setValue(len(items) + 1)
    except _Cancelled:
        result = DigitalPackageResult("cancelled", plan.format_id, None, issues=pre_issues)
    except Exception as error:
        logger.warning("digital package export failed", exc_info=True)
        result = DigitalPackageResult(
            "failed",
            plan.format_id,
            None,
            issues=(*pre_issues, PackageIssue("write_failed", detail=type(error).__name__)),
        )
    finally:
        if offscreen is not None:
            offscreen.release()
        close = getattr(progress, "close", None)
        if callable(close):
            close()
        shutil.rmtree(staging, ignore_errors=True)
    _show_summary(parent, result, message_box)
    return result


def _staging_dir(destination: Path) -> Path:
    staging = destination.parent / f".{destination.name}.staging-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    return staging


def _confirm_replace(
    parent: QWidget | None, destination: Path, format_id: str, message_box: Any
) -> bool:
    exists = destination.exists() and (
        format_id != "image_manifest_v1" or any(destination.iterdir())
    )
    if not exists:
        return True
    answer = message_box.question(
        parent,
        _ts("Export Digital Comic Package"),
        _ts("{name} already exists and will be replaced. Continue?").format(name=destination.name),
    )
    return answer == QMessageBox.StandardButton.Yes


def _default_progress_factory(parent: QWidget | None) -> Callable[[int], Any]:
    def factory(total: int) -> Any:
        dialog = QProgressDialog(_ts("Exporting package..."), _ts("Cancel"), 0, total, parent)
        dialog.setMinimumDuration(0)
        dialog.setWindowTitle(_ts("Export Digital Comic Package"))
        return dialog

    return factory


def _encode_items(
    page_service: Any,
    document_metadata: DocumentMetadata,
    plan: ExportDigitalPackagePlan,
    items: tuple[PublicationItem, ...],
    staging: Path,
    render_page: RenderPage,
    progress: Any,
) -> tuple[PublicationItem, ...]:
    encoded: list[PublicationItem] = []
    for index, item in enumerate(items):
        if progress.wasCanceled():
            raise _Cancelled
        progress.setLabelText(item.file_name)
        progress.setValue(index)
        page = page_service.get_page_snapshot(item.source_id)
        if page is None:
            raise ValueError(f"page not found: {item.source_id}")
        rgba = np.ascontiguousarray(render_page(document_metadata, page))
        target = staging / item.file_name
        page_canvas = getattr(page, "canvas", None)
        dpi = tuple(page_canvas.dpi) if page_canvas is not None else tuple(document_metadata.dpi)
        outcome = export_raster(
            rgba,
            target,
            plan.image_format,
            canvas_dpi=(float(dpi[0]), float(dpi[1])),
            background_rgba=tuple(document_metadata.background_color),
        )
        if getattr(outcome, "status", "success") == "failed":
            raise RuntimeError(f"encode failed: {item.file_name}")
        payload = target.read_bytes()
        encoded.append(
            item.with_content(
                content_hash=hashlib.sha256(payload).hexdigest(),
                pixel_size=(int(rgba.shape[1]), int(rgba.shape[0])),
                byte_size=len(payload),
            )
        )
        evict = getattr(page_service, "evict_pages_to_budget", None)
        if callable(evict):
            evict()
    return tuple(encoded)


def _write_and_validate(
    request: DigitalComicPackageRequest,
    staging: Path,
    validator: PublicationPackageValidator,
    epub_check: EpubCheckAdapter | None,
    pre_issues: tuple[PackageIssue, ...],
) -> DigitalPackageResult:
    def image_source(item: PublicationItem) -> Path:
        return staging / item.file_name

    issues = list(validator.validate_request(request))
    destination = Path(request.destination)
    if request.format_id == "image_manifest_v1":
        byte_count = ImageManifestPackageWriter().write(request, image_source)
        issues.extend(validator.validate_directory(destination, request))
        return _result(request, destination, byte_count, issues, "not_checked", "")
    staged = staging / ("package.epub" if request.format_id == "epub3_fxl" else "package.cbz")
    staged_request = DigitalComicPackageRequest(
        format_id=request.format_id,
        binding=request.binding,
        items=request.items,
        metadata=request.metadata,
        destination=staged,
        include_loomstroke_manifest=request.include_loomstroke_manifest,
        page_labels=request.page_labels,
    )
    if request.format_id == "epub3_fxl":
        byte_count = FixedLayoutEpubWriter().write(staged_request, image_source)
        archive_issues = list(
            validator.validate_archive(
                staged, staged_request, member_for=FixedLayoutEpubWriter.image_member
            )
        )
    else:
        byte_count = CbzPackageWriter().write(staged_request, image_source)
        archive_issues = list(validator.validate_archive(staged, staged_request))
    issues.extend(archive_issues)
    if any(issue.severity == "fatal" for issue in archive_issues):
        return DigitalPackageResult(
            "failed",
            request.format_id,
            None,
            item_count=len(request.items),
            issues=tuple(sorted(set((*pre_issues, *issues)), key=lambda i: (i.code, i.detail))),
            internal_validation="failed",
        )
    from ..io.atomic_export_writer import write_atomically  # noqa: PLC0415

    write_atomically(destination, lambda tmp: shutil.copyfile(staged, tmp))
    external, summary = "not_checked", ""
    if request.format_id == "epub3_fxl":
        adapter = epub_check or EpubCheckAdapter()
        external, summary = adapter.run(destination)
    return _result(request, destination, byte_count, issues, external, summary)


def _result(
    request: DigitalComicPackageRequest,
    destination: Path,
    byte_count: int,
    issues: list[PackageIssue],
    external: str,
    summary: str,
) -> DigitalPackageResult:
    fatal = any(issue.severity == "fatal" for issue in issues)
    return DigitalPackageResult(
        "failed" if fatal else "success",
        request.format_id,
        None if fatal else destination,
        item_count=len(request.items),
        byte_count=byte_count,
        issues=tuple(sorted(set(issues), key=lambda i: (i.code, i.detail))),
        internal_validation="failed" if fatal else "passed",
        external_validation=external,  # type: ignore[arg-type]
        external_summary=summary,
    )


def _show_summary(parent: QWidget | None, result: DigitalPackageResult, message_box: Any) -> None:
    title = _ts("Export Digital Comic Package")
    if result.status == "cancelled":
        return
    if result.status == "failed":
        codes = ", ".join(sorted({issue.code for issue in result.fatal_issues})) or "write_failed"
        message_box.warning(
            parent, title, _ts("The package could not be written: {codes}").format(codes=codes)
        )
        return
    text = _ts("Exported {count} page(s) to {name}.").format(
        count=result.item_count, name=result.file_name
    )
    warnings = sorted({issue.code for issue in result.issues if issue.severity != "fatal"})
    if warnings:
        text += "\n" + _ts("Warnings: {codes}").format(codes=", ".join(warnings))
    if result.format_id == "epub3_fxl":
        text += "\n" + (
            _ts("EPUB conformance checked (EPUBCheck).")
            if result.external_validation == "passed"
            else _ts("EPUB conformance not checked (EPUBCheck unavailable or failed).")
        )
    message_box.information(parent, title, text)
