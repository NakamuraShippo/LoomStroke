"""Small source previews for hidden content; no document or export mutation."""

from PySide6.QtCore import QPointF
from PySide6.QtGui import QTransform

from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.image_size.vector_geometry_scale_service import VectorGeometryScaleService
from loomstroke.core.snapshot_vector_rasterizer import SnapshotVectorRasterizer


class FrameGhostPainter:
    def __init__(self):
        self._vectors = {}

    def paint(self, painter, preview, item_id, viewport, origin):
        dx, dy = preview.moves.get(item_id, (0, 0))
        fit = preview.image_repairs.get(item_id)

        def world(point):
            x, y = viewport.canvas_to_widget(point[0] + dx, point[1] + dy)
            return QPointF(x + origin.x(), y + origin.y())

        def image_at(image, point):
            a, b, c = world(point(0, 0)), world(point(1, 0)), world(point(0, 1))
            painter.save()
            try:
                painter.setOpacity(0.35)
                painter.setWorldTransform(
                    QTransform(
                        b.x() - a.x(), b.y() - a.y(), c.x() - a.x(), c.y() - a.y(), a.x(), a.y()
                    )
                )
                painter.drawImage(QPointF(0, 0), image)
            finally:
                painter.restore()

        def visit(layer_id):
            part = preview.state.panel_parts[layer_id]
            values = part.values
            image = values["image"]
            if image is not None and not image.isNull():
                affine = (
                    fit.transform if fit and layer_id == item_id else values["transform"]
                ).to_affine()
                image_at(
                    image, lambda x, y: affine.apply(x + values["offset_x"], y + values["offset_y"])
                )
            if values["vector_objects"]:
                factor = min(1, 256 / max(preview.page_size))
                key = (layer_id, part.texture_version, preview.page_size)
                cached = self._vectors.get(key)
                if cached is None:
                    vectors = VectorGeometryScaleService().scale_objects(
                        values["vector_objects"], factor, factor
                    )
                    layer = _Layer(
                        id=layer_id,
                        name=layer_id,
                        layer_type="vector",
                        vector_objects=vectors,
                        vector_kind=values["vector_kind"],
                    )
                    cached = SnapshotVectorRasterizer(
                        width=max(1, round(preview.page_size[0] * factor)),
                        height=max(1, round(preview.page_size[1] * factor)),
                    ).rasterize(layer)
                    self._vectors[key] = cached
                image_at(cached, lambda x, y: (x / factor, y / factor))
            for child in part.children:
                visit(child)

        visit(item_id)

    def clear(self):
        self._vectors.clear()
