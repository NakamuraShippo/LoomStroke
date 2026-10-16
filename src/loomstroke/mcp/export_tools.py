"""File/export tools with explicit roots and overwrite handling."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import book_export_operations
from .editing_common import edit_operation, invalid
from .export_runtime import layered_export
from .export_tool_schemas import DIGITAL_SCHEMA, IMAGE_SCHEMA, PDF_SCHEMA, WEBTOON_SCHEMA
from .paint_extension_arguments import validate
from .raster_export_operation import SUFFIXES, raster_export
from .types import McpToolSpec

TOOLS = (
    McpToolSpec(
        "loomstroke_export_image",
        "Export active page as PNG/JPEG/TIFF/WebP/BMP through the GUI raster contract: "
        "ICC policy, DPI, alpha/matte and format-specific quality options. Raster encoding "
        "runs on a worker; maximum 16,777,216 canvas pixels. PSD uses the GUI layered subset; "
        "SVG exports compatible visible top-level vectors only. Layered/vector formats "
        "reject raster-only options.",
        IMAGE_SCHEMA,
    ),
    McpToolSpec(
        "loomstroke_export_print_pdf",
        "Export selected logical pages as the GUI's standard RGB print PDF, with optional "
        "crop marks. MediaBox only; this is not PDF/X. Maximum 5,000 pages and "
        "16,777,216 pixels per page; roots and overwrite consent required.",
        PDF_SCHEMA,
    ),
    McpToolSpec(
        "loomstroke_export_webtoon",
        "Export cropped/gapped page segments to fixed-height PNG/JPEG/WebP slices and "
        "optional manifest using the GUI webtoon runner. Supports no-split warnings, naming "
        "and collision policies. At most 5,000 segments, 10,000 slices and 16,777,216 pixels "
        "per page/scaled segment/slice. Slice boundaries remain unverified as in the GUI.",
        WEBTOON_SCHEMA,
    ),
    McpToolSpec(
        "loomstroke_export_digital_package",
        "Export selected logical pages as CBZ, fixed-layout EPUB3, or an image directory "
        "plus publication manifest. Reuses GUI internal archive validation; optional local "
        "EPUBCheck requires explicit runEpubCheck. Maximum 5,000 pages and 16,777,216 pixels "
        "per page; explicit publication metadata, roots and overwrite policy required.",
        DIGITAL_SCHEMA,
    ),
)


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = validate(TOOLS, name, arguments)
    async with edit_operation(service, name, args) as canvas:
        if name == "loomstroke_export_image":
            path = service.safety.validate_output_path(
                args["path"],
                overwrite=args.get("overwrite", False),
                allowed_suffixes=SUFFIXES[args["format"]],
            )
            if path.is_dir():
                raise invalid("An image export destination must be a file.", "path")
            if args["format"] in {"psd", "svg"}:
                return layered_export(service, path, args["format"])
            if "matte" in args and args.get("alphaMode", "preserve") != "matte":
                raise invalid("'matte' requires alphaMode=matte.", "matte")
            return await raster_export(service, canvas, path, args)
        if name == "loomstroke_export_print_pdf":
            return book_export_operations.print_pdf(service, args)
        if name == "loomstroke_export_webtoon":
            return book_export_operations.webtoon(service, args)
        return book_export_operations.digital_package(service, args)
