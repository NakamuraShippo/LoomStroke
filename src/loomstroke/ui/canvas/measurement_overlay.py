"""Display-only measurement line and source-template label."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QPainter, QPen

from ...core.measurement_geometry import Measurement
from ..translations import translate_with_fallback

LABEL_SOURCE = "Distance: {distance:.3f} {unit} | Angle: {angle:.2f}° | ΔX: {dx:.3f} | ΔY: {dy:.3f}"


def measurement_label(result: Measurement) -> str:
    return translate_with_fallback("MeasurementTool", LABEL_SOURCE).format(
        distance=result.distance,
        unit=result.unit,
        angle=result.angle,
        dx=result.dx,
        dy=result.dy,
    )


def draw_measurement(
    painter: QPainter,
    start: tuple[float, float],
    end: tuple[float, float],
    result: Measurement,
) -> None:
    painter.save()
    try:
        pen = QPen(QColor(255, 200, 50), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawLine(QPointF(*start), QPointF(*end))
        # Draw text in screen coordinates so zoom does not scale the label.
        label_pos = painter.transform().map(QPointF(*end))
        painter.resetTransform()
        painter.drawText(label_pos + QPointF(8, -8), measurement_label(result))
    finally:
        painter.restore()
