"""Screen-sized position label for the current guide drag."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainter

from ...core.guide_layout_geometry import unit_to_pixels
from ..translations import translate_with_fallback
from .guide_models import GuideOrientation, RulerState


def draw_guide_position(
    painter: QPainter,
    pointer: QPointF,
    value: float,
    orientation: GuideOrientation,
    ruler: RulerState,
    dpi: tuple[float, float],
) -> None:
    axis = 0 if orientation == "vertical" else 1
    origin = ruler.origin.x() if axis == 0 else ruler.origin.y()
    position = (value - origin) / unit_to_pixels(1.0, ruler.unit, dpi[axis])
    label = translate_with_fallback("GuidePositionOverlay", "{position:.3f} {unit}").format(
        position=position, unit=ruler.unit
    )
    painter.save()
    try:
        point = painter.transform().map(pointer) + QPointF(8, -8)
        painter.resetTransform()
        painter.drawText(point, label)
    finally:
        painter.restore()
