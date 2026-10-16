"""Roots-aware adapters for existing PDF, webtoon and digital package runners."""

from __future__ import annotations

import math
import os
from dataclasses import replace
from typing import Any

from loomstroke.core.book_layout_service import BookLayoutService
from loomstroke.core.digital_package.epub_check_adapter import EpubCheckAdapter
from loomstroke.core.digital_package.publication_metadata import PublicationMetadata
from loomstroke.core.digital_package.publication_planner import DigitalComicPackagePlanner
from loomstroke.core.export_collision_policy import ExportCollisionPolicy
from loomstroke.core.webtoon import WebtoonProfile, WebtoonSegment
from loomstroke.core.webtoon.fixed_height_slicer import FixedHeightSlicer
from loomstroke.core.webtoon.vertical_stream_resolver import VerticalStreamResolver
from loomstroke.core.webtoon.webtoon_manifest_writer import WebtoonManifestWriter
from loomstroke.ui.dialogs.export_digital_package_dialog import ExportDigitalPackagePlan
from loomstroke.ui.dialogs.export_webtoon_dialog import ExportWebtoonPlan
from loomstroke.ui.digital_package_export_runner import run_digital_package_export
from loomstroke.ui.print_pdf_export import export_print_pdf
from loomstroke.ui.webtoon_export_runner import _file_names, run_webtoon_export

from .editing_common import invalid
from .export_runtime import ExportMessages, ExportProgress, document_metadata, page_renderer
from .page_tools import require_page, require_pages
from .serialization import to_jsonable
from .types import McpStructuredError, ReasonCode

_MAX_PIXELS = 16_777_216


def _page_ids(pages: Any, requested: list[str] | None) -> list[str]:
    available = [page.id for page in pages.list_pages()]
    selected = available if requested is None else requested
    if len(selected) > 5000:
        raise invalid("One export supports at most 5,000 selected pages.", "pageIds")
    if not selected or any(page_id not in available for page_id in selected):
        raise invalid("Export requires existing selected pages.", "pageIds")
    ordered = [page_id for page_id in available if page_id in set(selected)]
    for page_id in ordered:
        page = require_page(pages, page_id)
        if page.canvas.width * page.canvas.height > _MAX_PIXELS:
            raise invalid("Each exported page must have at most 16,777,216 pixels.", "pageIds")
    return ordered


def _child(service: Any, directory: Any, relative: Any, overwrite: bool) -> Any:
    path = service.safety.validate_output_path(directory / relative, overwrite=overwrite)
    if not path.is_relative_to(directory):
        raise McpStructuredError(
            ReasonCode.PERMISSION_DENIED,
            "An export child resolves outside its destination directory.",
        )
    if path.is_dir():
        raise invalid("An export file path is a directory.", "path")
    return path


def print_pdf(service: Any, args: dict[str, Any]) -> dict[str, Any]:
    pages = require_pages(service)
    page_ids = _page_ids(pages, args.get("pageIds"))
    path = service.safety.validate_output_path(
        args["path"], overwrite=args.get("overwrite", False), allowed_suffixes=(".pdf",)
    )
    if path.is_dir():
        raise invalid("The PDF destination must be a file.", "path")
    metadata = document_metadata(service)
    render, release = page_renderer(service)
    messages = ExportMessages()
    try:
        result = export_print_pdf(
            None,
            pages,
            metadata,
            page_ids=page_ids,
            destination=path,
            with_marks=args.get("withMarks", True),
            render_page=render,
            progress_factory=ExportProgress,
            message_box=messages,
        )
    finally:
        release()
    return {
        **result,
        "path": str(path) if result["status"] == "success" else None,
        "messages": messages.messages,
        "limitations": {"pdfX": False, "pageBoxes": "MediaBox only", "colorSpace": "RGB"},
    }


def _segments(pages: Any, args: dict[str, Any]) -> tuple[WebtoonSegment, ...]:
    if "segments" in args and "pageIds" in args:
        raise invalid("Specify either pageIds or segments, not both.")
    source = args.get("segments") or [
        {"pageId": page_id} for page_id in _page_ids(pages, args.get("pageIds"))
    ]
    segments = []
    for raw in source:
        page = require_page(pages, raw["pageId"])
        if page.canvas.width * page.canvas.height > _MAX_PIXELS:
            raise invalid("Each source page must have at most 16,777,216 pixels.", "segments")
        try:
            segment = WebtoonSegment(
                page.id,
                page.canvas.width,
                page.canvas.height,
                tuple(raw["crop"]) if "crop" in raw else None,
                raw.get("gapBefore", 0),
                raw.get("gapAfter", 0),
                raw.get("include", True),
                tuple(tuple(item) for item in raw.get("noSplitRanges", [])),
            )
        except ValueError as exc:
            raise invalid(
                "Segment crop or protected ranges exceed the source page.", "segments"
            ) from exc
        segments.append(segment)
    if not any(segment.include for segment in segments):
        raise invalid("At least one webtoon segment must be included.", "segments")
    return tuple(segments)


def webtoon(service: Any, args: dict[str, Any]) -> dict[str, Any]:
    pages = require_pages(service)
    profile = WebtoonProfile.generic()
    width = args.get("targetWidth", profile.target_width_px)
    maximum = args.get("maxSliceHeight", profile.max_slice_height_px)
    minimum = args.get("minSliceHeight", min(profile.min_slice_height_px, maximum))
    if minimum > maximum or width * maximum > _MAX_PIXELS:
        raise invalid(
            "Slice height limits must be ordered and each slice buffer must not exceed "
            "16,777,216 pixels."
        )
    profile = replace(
        profile,
        target_width_px=width,
        max_slice_height_px=maximum,
        min_slice_height_px=minimum,
        max_file_bytes=args.get("maxFileBytes"),
    )
    segments = _segments(pages, args)
    for segment in segments:
        if (
            segment.include
            and width
            * VerticalStreamResolver.scaled_height(segment.crop_width, segment.crop_height, width)
            > _MAX_PIXELS
        ):
            raise invalid(
                "Each scaled source segment must not exceed 16,777,216 pixels.", "segments"
            )
    stream = VerticalStreamResolver().build(segments, target_width=width)
    if math.ceil(stream.height / maximum) > 10000:
        raise invalid("One webtoon export supports at most 10,000 slices.")
    slices = FixedHeightSlicer().plan(stream, max_height=maximum, min_height=minimum)
    policy = args.get("collisionPolicy", "replace" if args.get("overwrite", False) else "suffix")
    plan = ExportWebtoonPlan(
        segments,
        profile,
        width,
        maximum,
        args.get("format", "png"),
        args.get("namingTemplate", "{index}"),
        args.get("writeManifest", True),
        policy,
        args.get("episodeName", ""),
    )
    directory = service.safety.validate_output_path(args["path"], overwrite=True)
    if directory.exists() and not directory.is_dir():
        raise invalid("Webtoon destination must be a directory.", "path")
    try:
        names = _file_names(plan, slices, stream)
    except ValueError as exc:
        raise invalid(
            "The naming template is invalid or produces duplicate names.", "namingTemplate"
        ) from exc
    planned = [_child(service, directory, name, True) for name in names]
    for decision in ExportCollisionPolicy().resolve(planned, policy):
        if decision.destination is not None:
            _child(service, directory, decision.destination.name, args.get("overwrite", False))
    if plan.write_manifest:
        _child(service, directory, WebtoonManifestWriter.FILE_NAME, args.get("overwrite", False))
    metadata = document_metadata(service)
    render, release = page_renderer(service)
    messages = ExportMessages(args.get("overwrite", False))
    directory.mkdir(parents=True, exist_ok=True)
    try:
        result = run_webtoon_export(
            None,
            pages,
            metadata,
            plan,
            destination_dir=directory,
            render_page=render,
            progress_factory=ExportProgress,
            message_box=messages,
        )
    finally:
        release()
    return {
        **to_jsonable(result),
        "exported": len(result["exported"]),
        "failed": len(result["failed"]),
        "skipped": len(result["skipped"]),
        "path": str(directory),
        "messages": messages.messages,
    }


def digital_package(service: Any, args: dict[str, Any]) -> dict[str, Any]:
    pages = require_pages(service)
    page_ids = _page_ids(pages, args.get("pageIds"))
    format_id = args["format"]
    suffixes = {"cbz": (".cbz",), "epub3_fxl": (".epub",), "image_manifest_v1": None}
    destination = service.safety.validate_output_path(
        args["path"], overwrite=args.get("overwrite", False), allowed_suffixes=suffixes[format_id]
    )
    if destination.exists() and destination.is_dir() != (format_id == "image_manifest_v1"):
        raise invalid("The digital package destination has the wrong file/directory type.", "path")
    staging = destination.parent / f".{destination.name}.staging-{os.getpid()}"
    service.safety.validate_output_path(staging, overwrite=False)
    metadata = document_metadata(service)
    resolver = BookLayoutService()
    attributes = resolver.resolve_page_attributes_by_id(
        {page_id: pages.get_page_metadata(page_id) for page_id in page_ids}
    )
    layout = resolver.resolve_layout(metadata.metadata)
    spreads = resolver.resolve_spreads_from_metadata(
        [page.id for page in pages.list_pages()],
        metadata.metadata,
        {page.id: pages.get_page_metadata(page.id) for page in pages.list_pages()},
    )
    sides = {
        slot.page_id: slot.physical_side
        for spread in spreads
        for slot in spread.slots
        if slot.page_id in page_ids
    }
    cover = args.get("coverPageId")
    if cover is not None and cover not in page_ids:
        raise invalid("The cover page must be part of the selected export.", "coverPageId")
    publication_args = dict(args["metadata"])
    publication_args["creators"] = tuple(publication_args.get("creators", ()))
    try:
        publication = PublicationMetadata(**publication_args)
    except ValueError as exc:
        raise invalid("Publication metadata is invalid.", "metadata") from exc
    roles = {page_id: attribute.role for page_id, attribute in attributes.items()}
    image_format = args.get("imageFormat", "png")
    extension = "jpg" if image_format == "jpeg" else image_format
    items = DigitalComicPackagePlanner().plan(
        page_ids,
        roles_by_id=roles,
        spread_sides_by_id=sides,
        extension=extension,
        cover_page_id=cover,
    )
    if format_id == "image_manifest_v1":
        for item in items:
            _child(service, destination, item.package_path, args.get("overwrite", False))
        _child(service, destination, "publication_manifest.json", args.get("overwrite", False))
    plan = ExportDigitalPackagePlan(
        format_id,
        args.get("binding", layout.binding),
        publication,
        tuple(page_ids),
        roles,
        sides,
        cover,
        image_format,
        args.get("includeLoomStrokeManifest", True),
    )
    render, release = page_renderer(service)
    messages = ExportMessages(args.get("overwrite", False))
    epub_check = (
        EpubCheckAdapter()
        if args.get("runEpubCheck", False)
        else EpubCheckAdapter(which=lambda _name: None)
    )
    try:
        result = run_digital_package_export(
            None,
            pages,
            metadata,
            plan,
            destination=destination,
            render_page=render,
            progress_factory=ExportProgress,
            message_box=messages,
            epub_check=epub_check,
        )
    finally:
        release()
    return {**to_jsonable(result), "messages": messages.messages}
