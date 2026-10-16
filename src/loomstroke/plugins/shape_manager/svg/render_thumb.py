from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QPainter, QPixmap

logger = logging.getLogger(__name__)

QtSvgRenderer: Any
try:
    from PySide6.QtSvg import QSvgRenderer as QtSvgRenderer
except ImportError:  # pragma: no cover - optional dependency
    QtSvgRenderer = None


def render_svg_thumbnail(svg_text: str, *, size: int = 128) -> QPixmap | None:
    if QtSvgRenderer is None:
        logger.warning("QSvgRenderer not available; thumbnail generation skipped")
        return None
    renderer = QtSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    if not renderer.isValid():
        logger.warning("Invalid SVG renderer payload")
        return None

    view_box = renderer.viewBoxF()
    if view_box.isEmpty():
        view_box = renderer.defaultSize()
    width = max(1.0, float(view_box.width()))
    height = max(1.0, float(view_box.height()))

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    scale = min(size / width, size / height)
    offset_x = (size - width * scale) * 0.5
    offset_y = (size - height * scale) * 0.5
    painter.translate(offset_x, offset_y)
    painter.scale(scale, scale)
    renderer.render(painter)
    painter.end()
    return pixmap
