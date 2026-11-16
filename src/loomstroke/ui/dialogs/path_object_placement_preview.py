"""Draw path placement from one cached source image without editing the page."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QTransform

from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.snapshot_vector_rasterizer import SnapshotVectorRasterizer


class PathObjectPlacementPreview:
    def __init__(self, dialog, canvas):
        self.dialog = dialog
        self.canvas = canvas
        self.source = None
        self.image = None
        self.local = QTransform()
        self.frames = []

    def refresh(self):
        self.render_now()

    def close(self):
        self.source = None
        self.image = None
        self.frames = []

    def render_now(self):
        source = self.dialog._source
        if source is not self.source:
            self.source = source
            self.image = None
            self.local = QTransform()
            if source is not None:
                self._prepare_source(source)
        self.frames = self.dialog.frames() if self.image is not None else []
        self.canvas.update()

    def _prepare_source(self, source):
        cx, cy = source.center
        center_shift = QTransform().translate(-cx, -cy)
        if source.image is not None:
            affine = source.image_affine
            if affine is None:
                return
            self.image = source.image
            self.local = (
                QTransform().translate(*source.image_origin)
                * QTransform(affine.a, affine.c, affine.b, affine.d, affine.tx, affine.ty)
                * center_shift
            )
            return
        width, height = self.dialog.host.canvas.get_canvas_size()
        layer = _Layer("path-placement-preview", "Preview", layer_type="vector")
        layer.vector_objects = source.vector_objects or []
        result = SnapshotVectorRasterizer(width=width, height=height).rasterize_for_preview(layer)
        if result is not None:
            self.image = result.image
            self.local = QTransform().translate(result.origin_x, result.origin_y) * center_shift

    def paint(self, painter: QPainter):
        if self.image is None or not self.frames:
            return
        base = painter.worldTransform()
        painter.save()
        width, height = self.dialog.host.canvas.get_canvas_size()
        painter.setClipRect(QRectF(0, 0, width, height), Qt.ClipOperation.IntersectClip)
        painter.setOpacity(painter.opacity() * self.dialog.source_opacity())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for frame in self.frames:
            painter.setWorldTransform(self.local * frame * base)
            painter.drawImage(QPointF(), self.image)
        painter.restore()
