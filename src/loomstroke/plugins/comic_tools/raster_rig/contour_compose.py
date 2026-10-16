"""ROI rasterization of the unified contour; never warps raster tail pixels."""

import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen

from loomstroke.core.document_snapshot_builder import DocumentSnapshotBuilder

from .compose import RigComposer, _clip_rect, _over, _rect_union
from .contour_detail import ContourDetail
from .contour_geometry import ContourGeometry
from .contour_tip_detail import TailTipDetail
from .local_fill import attachment_colour

CONTOUR_MODE = "contour_rebuild"


class ContourComposer(RigComposer):
    def __post_init__(self):
        super().__post_init__()
        self.geometry = ContourGeometry.from_sources(self.body_rgba, self.guide, self.rig)
        self.guide = self.geometry.guide
        self.detail = ContourDetail(self.geometry)
        self.fidelity = 0.0
        self.tip_fidelity = 0.0
        self.tip_detail = TailTipDetail(self.tail_rgba, self.rig, self.geometry)
        self.output.fill(0)
        self._source_rect = _clip_rect(
            (*map(round, self.guide.origin), self.body_rgba.shape[1], self.body_rgba.shape[0]),
            *self.canvas_size,
        )
        self._work_rect = self._source_rect
        self.fill_rgb = None

    def update(self, pose, *, opening=None):
        del opening
        root, _, normal = self.guide.frame_at(pose.root_u)
        self.fill_rgb = attachment_colour(self.body_rgba, self.geometry.stroke, root, normal)
        path = self.geometry.path(pose.root_u, pose.tip, pose.bend)
        ink_path = None
        fill_path = path
        if self.fidelity > 0:
            ink_path, fill_path = self.detail.paths(
                path, pose.root_u, self.fidelity, fill=self.fill_rgb is not None
            )
        bounds = (
            (ink_path or path)
            .boundingRect()
            .united(fill_path.boundingRect())
            .adjusted(
                -self.geometry.width, -self.geometry.width, self.geometry.width, self.geometry.width
            )
        )
        if self.tip_fidelity > 0:
            bounds = bounds.united(self.tip_detail.bounds(self.geometry, pose))
        ox, oy = self.guide.origin
        x, y = math.floor(bounds.left() + ox), math.floor(bounds.top() + oy)
        rect = _clip_rect(
            (x, y, math.ceil(bounds.right() + ox) - x + 1, math.ceil(bounds.bottom() + oy) - y + 1),
            *self.canvas_size,
        )
        dirty = _clip_rect(_rect_union(self._work_rect, rect, self._source_rect), *self.canvas_size)
        if dirty is not None:
            dx, dy, dw, dh = dirty
            self.output[dy : dy + dh, dx : dx + dw] = 0
        self._work_rect = _rect_union(rect, self._source_rect)
        self._last_pose = pose
        if rect is not None:
            x, y, w, h = rect
            stroke = self._rasterize(ink_path or path, rect, stroke=True, area=ink_path is not None)
            self.tip_detail.apply(stroke, rect, self.geometry, pose, self.tip_fidelity)
            if self.fill_rgb is not None:
                fill = self._rasterize(fill_path, rect, stroke=False)
                self.tip_detail.apply(
                    fill, rect, self.geometry, pose, self.tip_fidelity, fill_colour=self.fill_rgb
                )
                _over(fill, stroke)
                stroke = fill
            self.output[y : y + h, x : x + w] = stroke
        return dirty

    def _rasterize(self, path, rect, *, stroke, area=False):
        x, y, w, h = rect
        image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.guide.origin[0] - x, self.guide.origin[1] - y)
        if stroke and not area:
            pen = QPen(QColor("white"), self.geometry.width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("white"))
        painter.drawPath(path)
        painter.end()
        rgba = DocumentSnapshotBuilder.qimage_to_array(image)
        colour = self.geometry.colour if stroke else (*self.fill_rgb, 255)
        rgba[:, :, 3] = np.rint(rgba[:, :, 3].astype(float) * colour[3] / 255).astype(np.uint8)
        rgba[:, :, :3] = colour[:3]
        rgba[rgba[:, :, 3] == 0, :3] = 0
        return rgba

    def body_region(self, rect):
        x, y, w, h = rect
        return self.output[y : y + h, x : x + w].copy()

    def body_output(self):
        return self.output.copy()

    def opening_rect(self):
        return self._work_rect

    def tail_rect(self):
        return None

    def preview_tail_patch(self):
        return None
