"""Prepare bounded, undoable local repairs for ordinary RGBA raster layers.

The inpaint pen captures and commits through this module; ``telea_pixels`` is
its fallback kernel when numba is unavailable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from .applied_layer_history import AppliedLayerHistoryAttempt
from .channels.channel_pixel_policy import ChannelPixelPolicy
from .layer_tree_service import LayerTreeQueryService
from .raster_edit_geometry import RasterEditGeometry, clip_signature, edit_coverage
from .raster_region_command import RasterRegionCommand, mix_coverage, preserve_pixel_policy


def telea_pixels(pixels: np.ndarray, mask: np.ndarray, radius: int) -> np.ndarray:
    """Inpaint premultiplied BGRA without changing pixels outside the mask."""
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 4:
        raise ValueError("Telea requires premultiplied BGRA8 pixels.")
    if mask.dtype != np.uint8 or mask.shape != pixels.shape[:2]:
        raise ValueError("Telea requires a matching Alpha8 mask.")
    if not 1 <= radius <= 15:
        raise ValueError("Telea radius must be between 1 and 15 pixels.")
    if not np.any(mask) or np.all(mask):
        raise ValueError("Telea requires both repair pixels and surrounding source pixels.")
    output = pixels.copy()
    output[:, :, :3] = cv2.inpaint(pixels[:, :, :3], mask, radius, cv2.INPAINT_TELEA)
    output[:, :, 3] = cv2.inpaint(pixels[:, :, 3], mask, radius, cv2.INPAINT_TELEA)
    np.minimum(output[:, :, :3], output[:, :, 3:4], out=output[:, :, :3])
    output[mask == 0] = pixels[mask == 0]
    return output


class TeleaInpaintCommand(RasterRegionCommand):
    """A local raster patch routed through the ordinary history stack."""


def submit_telea_command(canvas: Any, command: TeleaInpaintCommand) -> bool:
    attempt = AppliedLayerHistoryAttempt(canvas)
    if not command.apply(canvas):
        return False
    return attempt.record(command, "Inpaint Pen", confirmed=True)


@dataclass(frozen=True)
class TeleaJob:
    canvas_id: int
    canvas_size: tuple[int, int]
    layer_id: str
    image_key: int
    texture_version: int
    transform_version: int
    mapping: Any
    alpha_lock: bool
    clipping: bool
    clip_state: tuple[Any, ...]
    channels: tuple[int, ...] | None
    selection: QImage | None
    rect: QRect
    edit_rect: QRect
    before: QImage
    pixels: np.ndarray
    mask: np.ndarray
    coverage: np.ndarray
    radius: int


def _channels(canvas: Any) -> tuple[int, ...] | None:
    service = canvas.channels() if hasattr(canvas, "channels") else None
    return tuple(service.selected) if service is not None else None


def _selection(canvas: Any) -> QImage | None:
    getter = getattr(canvas, "get_selection_mask", None)
    return getter() if callable(getter) else None


def _target(canvas: Any) -> Any:
    layer = LayerTreeQueryService(canvas).get_active_layer()
    if (
        layer is None
        or layer.layer_type != "raster"
        or not isinstance(layer.image, QImage)
        or layer.image.isNull()
        or layer.locked
        or not layer.visible
        or layer.mask_edit_mode
    ):
        raise ValueError("Inpainting requires an unlocked visible raster layer.")
    if layer.native_cmyk is not None:
        raise ValueError("Inpainting currently supports RGB raster layers only.")
    return layer


def capture_telea_job(
    canvas: Any,
    points: tuple[tuple[float, float], ...],
    size: int,
    radius: int,
    *,
    context_padding: int | None = None,
) -> TeleaJob:
    """Capture source pixels and edit constraints on the Qt thread."""
    if not points or any(not all(math.isfinite(v) for v in point) for point in points):
        raise ValueError("Inpainting requires a finite brush stroke.")
    if (
        not 1 <= size <= 200
        or not 1 <= radius <= 15
        or (context_padding is not None and context_padding < 0)
    ):
        raise ValueError("Inpaint brush size or radius is out of range.")
    layer = _target(canvas)
    geometry = RasterEditGeometry.capture(layer)
    mapping = geometry.mapping
    if not (
        math.isclose(mapping.a, 1.0)
        and math.isclose(mapping.d, 1.0)
        and math.isclose(mapping.b, 0.0)
        and math.isclose(mapping.c, 0.0)
        and math.isclose(mapping.tx, round(mapping.tx))
        and math.isclose(mapping.ty, round(mapping.ty))
    ):
        raise ValueError("Inpainting requires an untransformed raster layer.")
    local = tuple((x - round(mapping.tx), y - round(mapping.ty)) for x, y in points)
    half = size / 2 + 1
    edit_rect = QRect(
        math.floor(min(x for x, _ in local) - half),
        math.floor(min(y for _, y in local) - half),
        math.ceil(max(x for x, _ in local) + half)
        - math.floor(min(x for x, _ in local) - half)
        + 1,
        math.ceil(max(y for _, y in local) + half)
        - math.floor(min(y for _, y in local) - half)
        + 1,
    ).intersected(QRect(0, 0, geometry.width, geometry.height))
    if edit_rect.isEmpty():
        raise ValueError("The inpaint stroke is outside the raster layer.")
    padding = max(radius * 3 + 3, context_padding or 0)
    rect = edit_rect.adjusted(-padding, -padding, padding, padding).intersected(
        QRect(0, 0, geometry.width, geometry.height)
    )
    mask = np.zeros((rect.height(), rect.width()), dtype=np.uint8)
    pixel_points = [(round(x - rect.x()), round(y - rect.y())) for x, y in local]
    cv2.circle(mask, pixel_points[0], max(1, size // 2), 255, -1)
    for start, end in pairwise(pixel_points):
        cv2.line(mask, start, end, 255, size, cv2.LINE_8)
    selection = _selection(canvas)
    coverage = edit_coverage(canvas, layer, geometry, rect, selection)
    mask[coverage == 0] = 0
    if not np.any(mask):
        raise ValueError("The inpaint stroke does not intersect an editable area.")
    before = layer.image.copy(rect)
    return TeleaJob(
        id(canvas),
        canvas.get_canvas_size(),
        layer.id,
        layer.image.cacheKey(),
        layer.texture_version,
        layer.transform_version,
        geometry.mapping,
        layer.alpha_lock,
        layer.clipping,
        clip_signature(canvas, layer),
        _channels(canvas),
        selection,
        rect,
        edit_rect,
        before,
        ChannelPixelPolicy.pixels(before),
        mask,
        coverage,
        radius,
    )


def telea_job_current(canvas: Any, job: TeleaJob) -> bool:
    """Check that the source, constraints, and active target still match."""
    try:
        if id(canvas) != job.canvas_id or canvas.get_canvas_size() != job.canvas_size:
            return False
        layer = _target(canvas)
        current_mapping = RasterEditGeometry.capture(layer).mapping
        clip_state = clip_signature(canvas, layer)
        channels = _channels(canvas)
        selection = _selection(canvas)
    except (AttributeError, RuntimeError, ValueError):
        return False
    return not (
        layer.id != job.layer_id
        or layer.image.cacheKey() != job.image_key
        or layer.texture_version != job.texture_version
        or layer.transform_version != job.transform_version
        or current_mapping != job.mapping
        or layer.alpha_lock != job.alpha_lock
        or layer.clipping != job.clipping
        or clip_state != job.clip_state
        or channels != job.channels
        or selection != job.selection
    )


def prepared_telea_command(
    canvas: Any,
    job: TeleaJob,
    output: np.ndarray,
    *,
    command_type: type[TeleaInpaintCommand] = TeleaInpaintCommand,
    kind: str = "telea_inpaint",
) -> TeleaInpaintCommand | None:
    """Reject stale worker results and build a single local history entry."""
    if not telea_job_current(canvas, job) or output.shape != job.pixels.shape:
        return None
    if output.dtype != np.uint8:
        return None
    layer = _target(canvas)
    after = preserve_pixel_policy(
        canvas,
        layer,
        job.before,
        mix_coverage(job.before, ChannelPixelPolicy.image(output), job.coverage),
    )
    relative = job.edit_rect.translated(-job.rect.x(), -job.rect.y())
    before_patch, after_patch = job.before.copy(relative), after.copy(relative)
    if before_patch == after_patch:
        return None
    return command_type(layer, job.edit_rect, before_patch, after_patch, kind=kind)
