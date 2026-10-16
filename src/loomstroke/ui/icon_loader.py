"""SVG icon loader utility for UI panels.

Loads icons from SVG files with fallback to Qt standard pixmaps.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap

from loomstroke.ui.theme import svg_icon_tint_color

logger = logging.getLogger(__name__)

try:
    from PySide6.QtSvg import QSvgRenderer as _QSvgRenderer
except ImportError:  # pragma: no cover
    _QSvgRenderer: Any = None  # type: ignore[no-redef]  # optional QtSvg fallback

_DEFAULT_ICON_SIZE = QSize(16, 16)


def tint_pixmap_for_current_theme(pixmap: QPixmap) -> None:
    """Apply the current theme's monochrome SVG tint in-place."""

    tint = svg_icon_tint_color()
    if tint is None or pixmap.isNull():
        return
    painter = QPainter(pixmap)
    try:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), tint)
    finally:
        painter.end()


def load_svg_icon(path: Path, size: QSize = _DEFAULT_ICON_SIZE) -> QIcon | None:
    """Load an SVG file as a QIcon.

    Args:
        path: Path to the SVG file.
        size: Desired icon size.

    Returns:
        QIcon if successful, None otherwise.
    """
    if _QSvgRenderer is None:
        return None
    if not path.is_file():
        return None
    try:
        renderer = _QSvgRenderer(str(path))
        if not renderer.isValid():
            return None
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        tint_pixmap_for_current_theme(pixmap)
        return QIcon(pixmap)
    except Exception:
        logger.debug("Failed to load SVG icon: %s", path, exc_info=True)
        return None


def load_svg_icon_from_data(data: str, size: QSize = _DEFAULT_ICON_SIZE) -> QIcon | None:
    """Load an SVG string as a QIcon.

    Args:
        data: SVG markup string.
        size: Desired icon size.

    Returns:
        QIcon if successful, None otherwise.
    """
    if _QSvgRenderer is None:
        return None
    try:
        renderer = _QSvgRenderer(QByteArray(data.encode("utf-8")))
        if not renderer.isValid():
            return None
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        tint_pixmap_for_current_theme(pixmap)
        return QIcon(pixmap)
    except Exception:
        logger.debug("Failed to load SVG icon from data", exc_info=True)
        return None
