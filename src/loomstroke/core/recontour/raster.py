"""Render into detached region images with the existing raster brush primitives."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, TypeAlias, cast

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter

from ..canvas_stroke_layer_paint_service import CanvasStrokeLayerPaintService
from ..canvas_stroke_stamp_brush_service import CanvasStrokeStampBrushService
from ..commands import StampBrushCommand, _qimage_to_numpy_view, _tone_params_from_brush
from ..native_cmyk.stroke_image import NativeStrokeImage
from ..native_cmyk.stroke_replay import render_native_stroke
from ..native_cmyk.tool_space import is_placed, stroke_region

StrokeImage: TypeAlias = QImage | NativeStrokeImage


def paste(target: StrokeImage, image: StrokeImage, x: int, y: int) -> None:
    if isinstance(target, NativeStrokeImage):
        target.paste(cast(NativeStrokeImage, image), x, y)
        return
    painter = QPainter(target)
    try:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(x, y, cast(QImage, image))
    finally:
        painter.end()


def crop(layer: Any, rect: QRect, *, mask: bool = False) -> StrokeImage:
    if not mask and getattr(layer, "native_cmyk", None) is not None:
        return NativeStrokeImage.crop(layer, rect)
    source = layer.layer_mask if mask else layer.image
    return cast(QImage, source.copy(rect.translated(-layer.offset_x, -layer.offset_y)))


def command_bounds(canvas: Any, cmd: Any) -> QRect | None:
    if isinstance(cmd, StampBrushCommand):
        bounds = canvas._compute_stamp_bounds(cmd.points, cmd.brush)
    else:
        bounds = canvas._compute_stroke_bounds(cmd.points, cmd.brush)
    layer = canvas._get_active_layer()
    if (
        bounds is not None
        and getattr(layer, "native_cmyk", None) is not None
        and not (layer.mask_edit_mode and layer.layer_mask is not None)
    ):
        if is_placed(layer):
            if isinstance(cmd, StampBrushCommand):
                bounds = canvas._compute_stamp_bounds(canvas._stamp_points(cmd), cmd.brush)
                if bounds is None:
                    return None
            local = stroke_region(layer, bounds)
            return None if local.isEmpty() else local
        bounds = bounds.intersected(
            QRect(layer.offset_x, layer.offset_y, layer.image.width(), layer.image.height())
        )
    return bounds


def stroke_pixel_bytes(layer: Any, *, mask: bool = False) -> int:
    native = getattr(layer, "native_cmyk", None)
    return 5 * native.inks.dtype.itemsize if native is not None and not mask else 4


def submit_live_stroke(canvas: Any, cmd: Any) -> bool:
    layer = canvas._get_active_layer()
    if (
        getattr(layer, "native_cmyk", None) is not None
        and not (layer.mask_edit_mode and layer.layer_mask is not None)
        and not canvas.channels().is_alpha(layer)
    ):
        return bool(canvas.native_cmyk().paint(cmd))
    submit = (
        canvas._submit_stamp_brush if isinstance(cmd, StampBrushCommand) else canvas._submit_paint
    )
    return bool(submit(cmd))


def render(
    canvas: Any,
    image: StrokeImage,
    region: QRect,
    commands: list[Any],
    tips: dict,
    *,
    alpha_lock: bool,
    mask: bool,
) -> StrokeImage:
    """No layer, history, or event mutations until every command has succeeded."""
    if isinstance(image, NativeStrokeImage):
        return render_native_stroke(canvas, image, region, commands, tips, alpha_lock=alpha_lock)
    layer = SimpleNamespace(image=image, alpha_lock=alpha_lock)
    paint = CanvasStrokeLayerPaintService(canvas)
    stamp = CanvasStrokeStampBrushService(canvas)
    selection, selection_bounds = canvas._selection_clip_state()
    selection_alpha = None
    if selection is not None:
        selection_alpha = canvas._selection_mask_to_numpy(selection).astype("float32") / 255.0
    for cmd in commands:
        bounds = command_bounds(canvas, cmd)
        if bounds is None:
            continue
        if selection_bounds is not None:
            bounds = bounds.intersected(selection_bounds)
        bounds = bounds.intersected(region)
        if bounds.isEmpty():
            continue
        local = bounds.translated(-region.x(), -region.y())
        saved = canvas._save_alpha(layer, local)
        if isinstance(cmd, StampBrushCommand):
            view = _qimage_to_numpy_view(layer.image)
            if view is None:
                raise ValueError("Invalid Recontour image")
            opacity = min(1.0, max(0.0, cmd.brush.opacity))
            flow = min(1.0, max(0.0, cmd.brush.flow))
            alpha_cache = {
                size: tip * opacity * flow
                for (path, hardness, size), tip in tips.items()
                if path == cmd.tip_path and hardness == cmd.brush.hardness
            }
            for point in cmd.points:
                stamp.apply_stamp_point(
                    view=view,
                    bounds=bounds,
                    point=point,
                    brush=cmd.brush,
                    tip_path=cmd.tip_path,
                    tone_params=_tone_params_from_brush(cmd.brush, white_level=255.0),
                    alpha_scale=opacity * flow,
                    alpha_cache=alpha_cache,
                    selection_alpha=selection_alpha,
                    layer_offset_x=region.x(),
                    layer_offset_y=region.y(),
                )
        elif selection is not None and selection_bounds is not None:
            paint.paint_into_selection(
                layer=layer,
                points=cmd.points,
                brush=cmd.brush,
                color=canvas._paint_color_from_brush(cmd.brush),
                bounds=bounds,
                local_bounds=local,
            )
        else:
            paint.paint_into_layer(
                layer=layer,
                points=cmd.points,
                brush=cmd.brush,
                color=canvas._paint_color_from_brush(cmd.brush),
                offset_x=region.x(),
                offset_y=region.y(),
            )
        canvas._restore_alpha(layer, local, saved)
        if mask:
            canvas._normalize_mask_to_grayscale(layer.image)
    return cast(QImage, layer.image)
