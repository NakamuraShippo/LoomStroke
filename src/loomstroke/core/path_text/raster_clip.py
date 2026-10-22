"""Bounded, read-only raster snapshots in visible document coordinates."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QTransform


def visible_snapshot(image, canvas_size, *, alpha, origin, affine):
    """Return a clipped trace image, or None when the source is already inside.

    The caller maps snapshot pixels back through canvas_size. The trace surface
    is at most 1024 x 1024 pixels, regardless of document size, before ranking.
    """
    if canvas_size is None:
        return None
    width, height = canvas_size
    if width <= 0 or height <= 0:
        return QImage()
    transform = (
        QTransform(affine.a, affine.c, affine.b, affine.d, affine.tx, affine.ty)
        if affine is not None
        else QTransform.fromTranslate(*origin)
    )
    bounds = transform.mapRect(QRectF(0, 0, image.width(), image.height()))
    canvas_rect = QRectF(0, 0, width, height)
    if canvas_rect.contains(bounds):
        return None
    if not canvas_rect.intersects(bounds):
        return QImage()
    ratio = min(1.0, 1024 / max(width, height))
    trace_width, trace_height = max(1, round(width * ratio)), max(1, round(height * ratio))
    snapshot = QImage(trace_width, trace_height, QImage.Format.Format_RGBA8888)
    snapshot.fill(Qt.GlobalColor.transparent)
    source = image
    if image.format() == QImage.Format.Format_Alpha8:
        source = image.convertToFormat(QImage.Format.Format_RGBA8888)
    elif not alpha:
        source = QImage(image.size(), QImage.Format.Format_RGBA8888)
        source.fill(Qt.GlobalColor.white)
        source.setAlphaChannel(image.convertToFormat(QImage.Format.Format_Grayscale8))
    painter = QPainter(snapshot)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.scale(trace_width / width, trace_height / height)
    painter.setWorldTransform(transform, True)
    painter.drawImage(QRectF(0, 0, image.width(), image.height()), source)
    painter.end()
    return snapshot
