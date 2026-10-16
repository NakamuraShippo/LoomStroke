"""Draw existing brush primitives into bounded patches of placed raster images."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter

from .canvas_command_commit_guard import CanvasCommandCommitGuard
from .commands import StampBrushCommand
from .placed_raster_stamp import stamp_placed_patch
from .raster_edit_geometry import RasterEditGeometry, edit_coverage
from .raster_region_command import mix_coverage, preserve_pixel_policy
from .raster_stroke_command import RasterStrokeCommand, current_placed_stroke


def apply_placed_stroke(  # noqa: PLR0911 - reject invalid/empty edits before committing pixels
    canvas: Any, layer: Any, command: Any, bounds: QRect, *, erase: bool = False
) -> QRect | None:
    try:
        geometry = RasterEditGeometry.capture(layer)
    except ValueError:
        return None
    local = geometry.source_rect(bounds)
    if local.isEmpty():
        return None
    selection, _ = canvas._selection_clip_state()
    coverage = edit_coverage(canvas, layer, geometry, local, selection)
    if not coverage.any():
        return None
    before = layer.image.copy(local)
    if isinstance(command, StampBrushCommand):
        after = stamp_placed_patch(command, geometry, local, before)
    else:
        after = _draw_patch(canvas, command, geometry, local, before, erase=erase)
    after = preserve_pixel_policy(
        canvas, layer, before, mix_coverage(before, after, coverage), erase=erase
    )
    if before == after:
        return None
    current = current_placed_stroke(canvas, layer, erase=erase)
    if current is not None:
        return (
            geometry.document_rect(local) if current.update(canvas, layer, local, after) else None
        )
    patch = RasterStrokeCommand(layer, erase=erase)
    patch.tiles = patch.prepare(layer, local, after)
    patch.local_dirty = local
    guard = CanvasCommandCommitGuard(canvas)
    try:
        if not patch.apply(canvas, refresh=False):
            return None
        canvas._record_history_command("Erase Stroke" if erase else "Paint Stroke", patch)
    except Exception:
        if patch._executed:
            guard.rollback(patch)
        raise
    return geometry.document_rect(local)


def _draw_patch(canvas, command, geometry, local, before, *, erase):
    after = before.copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(after)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setTransform(geometry.painter_transform(local))
    try:
        if erase:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
            color = QColor(255, 255, 255, round(255 * command.brush.opacity))
            canvas._draw_erase_stroke(painter, command.points, command.brush.size, color=color)
        else:
            canvas._draw_paint_stroke(
                painter,
                command.points,
                command.brush,
                canvas._paint_color_from_brush(command.brush),
            )
    finally:
        painter.end()
    return after
