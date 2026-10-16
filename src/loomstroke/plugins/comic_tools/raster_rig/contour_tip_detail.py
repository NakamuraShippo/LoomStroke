"""Morph terminal coverage with source signed distances, never source RGB seams."""

import cv2
import numpy as np
from PySide6.QtCore import QRectF

from .contour_tail_curve import middle_point, tail_curve
from .opening import stroke_mask


def _signed_distance(alpha):
    mask = (alpha >= 128).astype(np.uint8)
    inside = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    outside = cv2.distanceTransform(1 - mask, cv2.DIST_L2, 5)
    distance = np.where(mask > 0, inside - 0.5, 0.5 - outside)
    edge = (alpha > 0) & (alpha < 255)
    distance[edge] = alpha[edge] / 255.0 - 0.5
    return distance.astype(np.float32)


class TailTipDetail:
    def __init__(self, rgba, rig, geometry):
        ink, _ = stroke_mask(rgba)
        alpha = np.where(ink > 0.5, rgba[:, :, 3], 0).astype(np.uint8)
        alpha = np.rint(alpha.astype(float) * 255 / max(1, int(alpha.max()))).astype(np.uint8)
        self.ink = _signed_distance(alpha)
        contours, _ = cv2.findContours(
            (alpha >= 128).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        filled = np.zeros_like(alpha)
        if contours:
            cv2.fillConvexPoly(filled, cv2.convexHull(max(contours, key=cv2.contourArea)), 255)
        self.fill = _signed_distance(filled)
        self.rig = rig
        self.available = bool(contours)
        self._prepare_body_protection(geometry)

    def _prepare_body_protection(self, geometry):
        points = geometry.guide.outline
        x, y, w, h = cv2.boundingRect(points.astype(np.float32))
        pad = int(np.ceil(max(geometry.width, float(geometry.source_widths.max())))) + 2
        self.body_origin = np.asarray((x - pad, y - pad)) + geometry.guide.origin
        self.body_protect = np.zeros((h + pad * 2, w + pad * 2), np.uint8)
        polygon = np.rint(points - (x - pad, y - pad)).astype(np.int32)
        cv2.fillPoly(self.body_protect, [polygon], 255)
        self.body_protect = cv2.dilate(
            self.body_protect,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (pad * 2 + 1, pad * 2 + 1)),
        )

    def bounds(self, geometry, pose):
        root = np.asarray(geometry.guide.point_at(pose.root_u)) + geometry.guide.origin
        length = max(1, float(np.linalg.norm(np.asarray(pose.tip) - root)))
        radius = min(
            length * 0.28,
            max(geometry.width * 3, self.rig.rest_axis[1] * self.rig.source_to_rig_scale * 0.25),
        )
        tip = np.asarray(pose.tip) - geometry.guide.origin
        return QRectF(tip[0] - radius - 2, tip[1] - radius - 2, radius * 2 + 4, radius * 2 + 4)

    def _mapping(self, rect, geometry, pose):
        tip = np.asarray(pose.tip)
        root = np.asarray(geometry.guide.point_at(pose.root_u)) + geometry.guide.origin
        axis = tip - root
        length = max(1, float(np.linalg.norm(axis)))
        axis /= length
        side = np.array((-axis[1], axis[0]))
        source_axis, source_length = self.rig.rest_axis
        source_axis = np.asarray(source_axis)
        source_side = np.array((-source_axis[1], source_axis[0]))
        scale = self.rig.source_to_rig_scale
        radius = min(length * 0.28, max(geometry.width * 3, source_length * scale * 0.25))
        x0 = max(rect[0], int(np.floor(tip[0] - radius - 2)))
        y0 = max(rect[1], int(np.floor(tip[1] - radius - 2)))
        x1 = min(rect[0] + rect[2], int(np.ceil(tip[0] + radius + 2)))
        y1 = min(rect[1] + rect[3], int(np.ceil(tip[1] + radius + 2)))
        if x1 <= x0 or y1 <= y0:
            return None
        y, x = np.mgrid[y0:y1, x0:x1]
        points = np.stack((x, y), axis=-1).astype(float)
        distance = np.linalg.norm(points - tip, axis=-1)
        weight = np.clip((radius - distance) / max(1, radius * 0.45), 0, 1)
        weight *= (points - root) @ axis > length * 0.65
        weight = weight * weight * (3 - 2 * weight)
        protected = cv2.remap(
            self.body_protect,
            (points[:, :, 0] - self.body_origin[0]).astype(np.float32),
            (points[:, :, 1] - self.body_origin[1]).astype(np.float32),
            cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        weight[protected > 0] = 0
        if pose.bend is not None:
            default = tail_curve(geometry, pose.root_u, pose.tip).middle() + geometry.guide.origin
            delta = np.asarray(middle_point(geometry, pose)) - default
            t = np.clip((points - root) @ axis / length, 0, 1)
            points -= delta * (16 * t * t * (1 - t) ** 2)[..., None]
        relative = points - tip
        source = (
            self.rig.tip
            + (
                (relative @ axis)[..., None] * source_axis
                + (relative @ side)[..., None] * source_side
            )
            / scale
        )
        region = (slice(y0 - rect[1], y1 - rect[1]), slice(x0 - rect[0], x1 - rect[0]))
        return source, weight, region, radius

    def apply(self, rgba, rect, geometry, pose, strength, *, fill_colour=None):
        if strength <= 0 or not self.available:
            return
        mapping = self._mapping(rect, geometry, pose)
        if mapping is None:
            return
        source, weight, region_slices, radius = mapping
        scale = self.rig.source_to_rig_scale
        sampled = (
            cv2.remap(
                self.fill if fill_colour is not None else self.ink,
                source[:, :, 0].astype(np.float32),
                source[:, :, 1].astype(np.float32),
                cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=-radius / scale,
            )
            * scale
        )
        region = rgba[region_slices]
        opacity = geometry.colour[3] if fill_colour is None else 255
        coverage = np.clip(region[:, :, 3].astype(float) * 255 / max(1, opacity), 0, 255)
        clean = _signed_distance(coverage)
        weight = strength * weight
        # Interpolating distances gives one boundary instead of double AA edges.
        combined = clean * (1 - weight) + sampled * weight
        alpha = np.rint(np.clip(combined + 0.5, 0, 1) * opacity).astype(np.uint8)
        active = weight > 0
        region[:, :, 3][active] = alpha[active]
        region[:, :, :3][active] = geometry.colour[:3] if fill_colour is None else fill_colour
        region[region[:, :, 3] == 0, :3] = 0
