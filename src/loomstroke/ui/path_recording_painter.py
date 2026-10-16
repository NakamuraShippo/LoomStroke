"""A QPainter stand-in that records drawn text as glyph outlines (CT-25 F4).

The text render primitives draw through a small QPainter subset
(``save`` / ``restore`` / ``translate`` / ``rotate`` / ``scale`` /
``setFont`` / ``drawText`` ...).  This recorder implements exactly that
surface: every ``drawText`` becomes ``QPainterPath.addText`` under the
current transform, every other drawing call is a no-op, so the real
layout code (horizontal, vertical, wrap, tracking, rotation) produces
the outline with no second layout implementation.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QBrush, QFont, QPainterPath, QPen, QTransform

__all__ = ["PathRecordingPainter"]


class PathRecordingPainter:
    def __init__(self, font: QFont | None = None) -> None:
        self._transform = QTransform()
        self._stack: list[tuple[QTransform, QFont, QPen, QBrush]] = []
        self._font = QFont(font) if font is not None else QFont()
        self._pen = QPen()
        self._brush = QBrush()
        self.path = QPainterPath()
        self.glyph_runs = 0

    # -- state ---------------------------------------------------------------------

    def save(self) -> None:
        self._stack.append(
            (QTransform(self._transform), QFont(self._font), QPen(self._pen), QBrush(self._brush))
        )

    def restore(self) -> None:
        if self._stack:
            self._transform, self._font, self._pen, self._brush = self._stack.pop()

    def setWorldTransform(self, transform: QTransform, combine: bool = False) -> None:  # noqa: N802
        self._transform = (
            QTransform(transform) * self._transform if combine else QTransform(transform)
        )

    def translate(self, dx: Any, dy: Any = None) -> None:
        if dy is None:  # QPointF form
            dx, dy = dx.x(), dx.y()
        self._transform = QTransform().translate(float(dx), float(dy)) * self._transform

    def rotate(self, angle: float) -> None:
        self._transform = QTransform().rotate(float(angle)) * self._transform

    def scale(self, sx: float, sy: float) -> None:
        self._transform = QTransform().scale(float(sx), float(sy)) * self._transform

    def setFont(self, font: QFont) -> None:  # noqa: N802
        self._font = QFont(font)

    def font(self) -> QFont:
        return QFont(self._font)

    def setPen(self, pen: Any) -> None:  # noqa: N802
        self._pen = pen if isinstance(pen, QPen) else QPen(pen)

    def pen(self) -> QPen:
        return QPen(self._pen)

    def setBrush(self, brush: Any) -> None:  # noqa: N802
        self._brush = brush if isinstance(brush, QBrush) else QBrush(brush)

    # -- drawing -------------------------------------------------------------------

    def drawText(self, *args: Any) -> None:  # noqa: N802
        if not args:
            return
        first = args[0]
        if isinstance(first, QPointF):
            text = str(args[1]) if len(args) > 1 else ""
            origin = first
        elif isinstance(first, QRectF | tuple):
            # Rect / flags form is never used by the text primitives for glyphs.
            return
        elif len(args) >= 3:
            origin = QPointF(float(first), float(args[1]))
            text = str(args[2])
        else:
            return
        if not text:
            return
        glyphs = QPainterPath()
        glyphs.addText(origin, self._font, text)
        self.path.addPath(self._transform.map(glyphs))
        self.glyph_runs += 1

    # No-ops for chrome the primitives draw around text.
    def drawLine(self, *args: Any) -> None:  # noqa: N802
        return None

    def drawRect(self, *args: Any) -> None:  # noqa: N802
        return None

    def fillRect(self, *args: Any) -> None:  # noqa: N802
        return None

    def drawEllipse(self, *args: Any) -> None:  # noqa: N802
        return None

    def drawPolygon(self, *args: Any) -> None:  # noqa: N802
        return None

    def drawPath(self, *args: Any) -> None:  # noqa: N802
        return None

    def setClipRect(self, *args: Any) -> None:  # noqa: N802
        return None

    def setClipPath(self, *args: Any) -> None:  # noqa: N802
        return None

    def setRenderHint(self, *args: Any) -> None:  # noqa: N802
        return None

    def setOpacity(self, *args: Any) -> None:  # noqa: N802
        return None

    def setCompositionMode(self, *args: Any) -> None:  # noqa: N802
        return None
