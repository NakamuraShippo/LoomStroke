"""Reconstruct one closed body/tail contour from the original contour lab."""

from dataclasses import dataclass

import cv2
import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath

from .compose import TailPose
from .contour_tail_curve import middle_point, tail_curve
from .guide import AttachmentGuide, _finish_guide, _resample_closed, _smooth_closed
from .opening import stroke_mask


def _main_ring(mask):
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hierarchy is None:
        return None
    outer_i = max(range(len(contours)), key=lambda i: cv2.contourArea(contours[i]))
    children = [i for i in range(len(contours)) if hierarchy[0, i, 3] == outer_i]
    if not children:
        return None
    inner_i = max(children, key=lambda i: cv2.contourArea(contours[i]))
    if cv2.contourArea(contours[inner_i]) < cv2.contourArea(contours[outer_i]) * 0.1:
        return None
    return contours[outer_i], contours[inner_i]


@dataclass
class ContourGeometry:
    guide: AttachmentGuide
    width: float
    colour: tuple[int, int, int, int]
    gap: float
    bend: float
    stroke: np.ndarray
    source_centres: np.ndarray
    source_widths: np.ndarray

    @staticmethod
    def body_components(body, origin):
        return ContourGeometry._body_measurements(body, origin)[:4]

    @staticmethod
    def _body_measurements(body, origin):
        alpha = body[:, :, 3]
        mask = (alpha > max(8, int(alpha.max()) // 2)).astype(np.uint8)
        stroke, _ = stroke_mask(body)
        ring = _main_ring(mask)
        if ring is None:
            # Filled light bodies: extract the ink ring instead of treating the
            # width of the entire filled silhouette as the pen width.
            mask &= (stroke > 0.5).astype(np.uint8)
            ring = _main_ring(mask)
        if ring is None:
            raise ValueError("A closed ink ring is required for contour reconstruction.")
        outer_contour, inner_contour = ring
        outer = _resample_closed(outer_contour[:, 0, :], 3.0)
        inner = inner_contour[:, 0, :].astype(float)
        nearest = np.asarray([inner[np.argmin(np.sum((inner - p) ** 2, axis=1))] for p in outer])
        widths = np.linalg.norm(outer - nearest, axis=1)
        width = float(np.median(widths))
        bounds = cv2.boundingRect(outer_contour)
        if width > min(bounds[2:]) * 0.2:
            raise ValueError("The body ink ring is too ambiguous to reconstruct safely.")
        centre = _finish_guide(origin, _smooth_closed((outer + nearest) / 2, 1.2))
        inside = cv2.erode(mask, np.ones((5, 5), np.uint8)) > 0
        pixels = body[inside] if inside.any() else body[mask > 0]
        colour = tuple(int(v) for v in np.median(pixels, axis=0))
        return centre, width, colour, stroke, (outer + nearest) / 2, widths

    @classmethod
    def from_sources(cls, body, guide, rig):
        centre, width, colour, stroke, points, widths = cls._body_measurements(body, guide.origin)
        scaled = rig.rig_space()
        axis, length = scaled.rest_axis
        side = np.array((-axis[1], axis[0]))
        mid = scaled.spine[len(scaled.spine) // 2] - np.asarray(scaled.base_centre)
        bend = float(np.clip(mid @ side / max(length, 1), -0.25, 0.25))
        return cls(
            centre,
            width,
            colour,
            max(float(scaled.base_width), width * 2.5),
            bend,
            stroke,
            points,
            widths,
        )

    def clamp_tip(self, root_u, tip):
        root, _, normal = self.guide.frame_at(root_u)
        root = np.asarray(root) + self.guide.origin
        normal = np.asarray(normal)
        depth = float((np.asarray(tip) - root) @ normal)
        minimum = max(self.width * 3, self.gap * 0.6)
        return tuple(np.asarray(tip) + normal * max(0, minimum - depth))

    def path(self, root_u, tip, bend=None):
        g = self.guide
        half = min(0.09, self.gap / (2 * g.perimeter))
        ua, ub = root_u - half, root_u + half
        curve = tail_curve(self, root_u, tip)
        path = QPainterPath(QPointF(*curve.second[-1]))
        for u in np.linspace(ub, ua + 1, 600)[1:]:
            path.lineTo(QPointF(*g.point_at(float(u))))
        target = (
            None
            if bend is None
            else np.asarray(middle_point(self, TailPose(root_u, tip, bend))) - g.origin
        )
        curve.append(path, target)
        path.closeSubpath()
        return path
