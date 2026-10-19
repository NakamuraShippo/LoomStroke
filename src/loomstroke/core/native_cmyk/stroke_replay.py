"""Replay captured pen geometry into detached native-ink regions."""

from types import SimpleNamespace
from typing import Any

import numpy as np
from PySide6.QtCore import QRect

from .pixel_operations import NativeCmykPixelOperations
from .stroke_coverage import NativeCmykStrokeCoverage
from .stroke_image import NativeStrokeImage
from .tool_space import is_placed, local_paint


def render_native_stroke(
    canvas: Any,
    image: NativeStrokeImage,
    region: QRect,
    commands: list[Any],
    tips: dict[Any, Any],
    *,
    alpha_lock: bool,
) -> NativeStrokeImage:
    actual_layer = canvas._get_active_layer()
    placed = is_placed(actual_layer)
    layer = (
        actual_layer
        if placed
        else SimpleNamespace(image=image, offset_x=region.x(), offset_y=region.y())
    )
    for command in commands:
        rendered = NativeCmykStrokeCoverage.render(canvas, command, layer, tips=tips)
        if rendered is None:
            continue
        bounds, coverage, strength = rendered
        sample = getattr(command, "_native_ink_color", None)
        if sample is None:
            raise ValueError("Native stroke ink sample was not captured")
        values = np.asarray(sample, np.uint32)
        color = ((values * strength[:, :, None] + 127) // 255).astype(image.inks.dtype)
        if placed:
            bounds, color, coverage = local_paint(actual_layer, bounds, color, coverage)
            bounds = bounds.translated(actual_layer.offset_x, actual_layer.offset_y)
        local = bounds.translated(-region.topLeft())
        overlap = local.intersected(QRect(0, 0, image.width(), image.height()))
        if overlap.isEmpty():
            continue
        if overlap != local:
            dx, dy = overlap.x() - local.x(), overlap.y() - local.y()
            coverage = coverage[dy : dy + overlap.height(), dx : dx + overlap.width()]
            color = color[dy : dy + overlap.height(), dx : dx + overlap.width()]
            local = overlap
        before = image.copy(local).snapshot()
        after = NativeCmykPixelOperations.paint(
            before,
            color,
            coverage,
            getattr(command, "_native_plates", (0, 1, 2, 3)),
            alpha_lock=alpha_lock,
        )
        image.paste(
            NativeStrokeImage(after.inks.copy(), after.alpha.copy(), after.profile),
            local.x(),
            local.y(),
        )
    return image
