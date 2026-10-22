"""Arc-length intervals and bounded nearest-point queries for a copied contour."""

import math
from bisect import bisect_right
from functools import lru_cache

import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtGui import QTransform

from ..vector_text_layout import TextLayoutCalculator as Layout
from .metadata import path_settings


@lru_cache(maxsize=64)
def _sample(anchors, closed, width, height):
    values = []
    for pos, hin, hout in anchors:
        anchor = {"pos": pos}
        if hin is not None:
            anchor["h_in"] = hin
        if hout is not None:
            anchor["h_out"] = hout
        values.append(anchor)
    path = Layout.text_shape_path_from_paths(
        {"paths": [{"anchors": values, "closed": closed}], "handle_space": "anchor_delta"},
        x=0,
        y=0,
        w=width,
        h=height,
    )
    length = path.length() if path is not None else 0.0
    if length < 1e-6:
        return None
    count = min(4096, max(64, math.ceil(length / 2)))
    # QPainterPath's Bezier parameter is not a distance fraction. Sample the
    # parameter once, then use the resulting cumulative arc-length table.
    points = np.array(
        [(p.x(), p.y()) for p in (path.pointAtPercent(i / count) for i in range(count + 1))]
    )
    distances = np.concatenate(([0.0], np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))))
    return points, distances


class PathTrack:
    def __init__(self, settings, samples, origin):
        self.settings = settings
        self.points, self.distances = samples
        self.origin = QPointF(*origin)
        self.length = float(self.distances[-1])
        self.closed = bool(settings["path"].get("closed"))
        self.start = float(settings.get("start", 0))
        self.end = float(settings.get("end", 1))
        self.direction = -1 if settings.get("reverse") else 1
        if not self.closed:
            self.direction = 1 if self.end >= self.start else -1
            fraction = abs(self.end - self.start)
        elif settings.get("full_loop"):
            fraction = 1.0
        else:
            fraction = ((self.end - self.start) * self.direction) % 1.0
        self.interval_length = fraction * self.length

    @classmethod
    def from_payload(cls, payload, *, rect=None):
        settings = path_settings(payload)
        if settings is None:
            return None
        bounds = Layout.text_bbox_from_payload(payload)
        if bounds is None:
            return None
        if rect is None:
            x, y, width, height = bounds[0], bounds[1], bounds[2] - bounds[0], bounds[3] - bounds[1]
        else:
            x, y, width, height = rect.x(), rect.y(), rect.width(), rect.height()
        if not all(math.isfinite(v) for v in (x, y, width, height)) or width <= 0 or height <= 0:
            return None
        path = settings["path"]
        anchors = tuple(
            (
                tuple(a["pos"]),
                tuple(a["h_in"]) if a.get("h_in") else None,
                tuple(a["h_out"]) if a.get("h_out") else None,
            )
            for a in path["anchors"]
        )
        samples = _sample(anchors, bool(path.get("closed")), width, height)
        return cls(settings, samples, (x, y)) if samples is not None else None

    def at_fraction(self, fraction):
        distance = max(0, min(1, fraction)) * self.length
        index = min(len(self.points) - 2, max(0, bisect_right(self.distances, distance) - 1))
        step = self.points[index + 1] - self.points[index]
        span = self.distances[index + 1] - self.distances[index]
        t = (distance - self.distances[index]) / span if span > 1e-12 else 0
        point = self.points[index] + step * t
        return self.origin + QPointF(*point), math.degrees(math.atan2(step[1], step[0]))

    def frame_at(self, distance):
        fraction = self.start + self.direction * distance / self.length
        if self.closed:
            fraction %= 1.0
        point, angle = self.at_fraction(fraction)
        if self.direction < 0:
            angle += 180
        radians = math.radians(angle)
        offset = float(self.settings.get("offset", 0))
        point += QPointF(-math.sin(radians) * offset, math.cos(radians) * offset)
        transform = QTransform()
        transform.translate(point.x(), point.y())
        if self.settings.get("rotate", True):
            transform.rotate(angle)
        return transform

    def project(self, point):
        local = np.array([point.x() - self.origin.x(), point.y() - self.origin.y()])
        starts, steps = self.points[:-1], np.diff(self.points, axis=0)
        squared = np.sum(steps * steps, axis=1)
        t = np.clip(np.sum((local - starts) * steps, axis=1) / np.maximum(squared, 1e-20), 0, 1)
        nearest = starts + steps * t[:, None]
        index = int(np.argmin(np.sum((nearest - local) ** 2, axis=1)))
        return float(
            (self.distances[index] + t[index] * (self.distances[index + 1] - self.distances[index]))
            / self.length
        )
