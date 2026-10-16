"""Capture on the Qt thread, then run the shared raster encoder on a worker."""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np

from loomstroke.io.export_color_policy import ExportColorPolicy
from loomstroke.io.raster_export import export_raster

from .composite_snapshot import composite_image
from .editing_common import current_revision, invalid
from .extension_services import get_services
from .safety import validate_output_path
from .serialization import qimage_to_rgba_array, to_jsonable
from .types import McpStructuredError, ReasonCode

SUFFIXES = {
    "png": (".png",),
    "jpeg": (".jpg", ".jpeg"),
    "tiff": (".tif", ".tiff"),
    "webp": (".webp",),
    "bmp": (".bmp",),
    "psd": (".psd",),
    "svg": (".svg",),
}


async def current_pixels(service: Any) -> np.ndarray:
    canvas = service.canvas_service
    if canvas._width * canvas._height > 16_777_216:
        raise invalid("MCP raster export supports at most 16,777,216 canvas pixels.")
    callback = get_services(service).get("render_current")
    if callable(callback):
        rgba = callback()
    else:
        rgba = qimage_to_rgba_array(await composite_image(service))
    array = np.asarray(rgba)
    if (
        array.dtype != np.uint8
        or array.ndim != 3
        or array.shape[2] != 4
        or array.shape[:2] != (canvas._height, canvas._width)
    ):
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED, "The export renderer returned invalid RGBA8 pixels."
        )
    return array.copy()


async def raster_export(
    service: Any, canvas: Any, path: Any, args: dict[str, Any]
) -> dict[str, Any]:
    rgba = await current_pixels(service)
    profile = getattr(canvas, "color_profile", None)
    profile_bytes = bytes(profile.data) if profile else None
    dpi = float(canvas._dpi)
    background = tuple(canvas._background.getRgb())
    roots = tuple(service.safety.roots)
    captured_revision = current_revision(service)

    def encode() -> dict[str, Any]:
        destination = validate_output_path(
            path,
            roots=roots,
            overwrite=args.get("overwrite", False),
            allowed_suffixes=SUFFIXES[args["format"]],
        )
        resolved = ExportColorPolicy().resolve(
            args.get("colorPolicy", "embed"), document_profile=profile_bytes
        )
        pixels = resolved.transform(rgba) if resolved.transform is not None else rgba
        outcome = export_raster(
            pixels,
            destination,
            args["format"],
            request_dpi=args.get("dpi"),
            canvas_dpi=dpi,
            icc_bytes=resolved.icc_bytes,
            alpha_mode=args.get("alphaMode", "preserve"),
            matte_rgba=tuple(args["matte"]) if "matte" in args else None,
            background_rgba=background,
            encoder_options=args.get("encoderOptions", {}),
        )
        issues = (*resolved.issues, *outcome.issues)
        return {
            "status": (
                "failed" if outcome.status == "failed" else "warning" if issues else "success"
            ),
            "path": str(destination) if outcome.destination else None,
            "bytesWritten": outcome.bytes_written,
            "issues": to_jsonable(issues),
            "capturedRevision": captured_revision,
        }

    return await asyncio.to_thread(encode)
