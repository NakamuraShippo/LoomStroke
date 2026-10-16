"""Tail-only curve and its middle handle; root and tip tangents stay intact."""

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF


def _cubic(points, t):
    a, b, c, d = points
    return (1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t * t * c + t**3 * d


@dataclass
class TailCurve:
    first: tuple
    second: tuple

    def middle(self):
        return (_cubic(self.first, 0.5) + _cubic(self.second, 0.5)) / 2

    def append(self, path, target=None):
        offset = np.zeros(2) if target is None else np.asarray(target) - self.middle()
        for points in (self.first, self.second):
            if np.linalg.norm(offset) < 1e-8:
                path.cubicTo(*(QPointF(*p) for p in points[1:]))
            else:
                # Quartic bump is zero with zero derivative at BOTH ends.
                # The join and terminal tangent are unaffected by bending.
                for t in np.linspace(0, 1, 65)[1:]:
                    p = _cubic(points, t) + offset * 16 * t * t * (1 - t) ** 2
                    path.lineTo(QPointF(*p))


def tail_curve(geometry, root_u, tip):
    g = geometry.guide
    tip = np.asarray(tip) - g.origin
    half = min(0.09, geometry.gap / (2 * g.perimeter))
    a, ta, _ = g.frame_at(root_u - half)
    b, tb, _ = g.frame_at(root_u + half)
    a, b, ta, tb = map(np.asarray, (a, b, ta, tb))
    root = np.asarray(g.point_at(root_u))
    axis = tip - root
    length = max(1, float(np.linalg.norm(axis)))
    side = np.array((-axis[1], axis[0])) / length
    bend = side * geometry.bend * length
    side_a = side * (1 if float((a - root) @ side) >= 0 else -1)
    reach = min(length * 0.32, geometry.gap * 1.2)
    return TailCurve(
        (a, a + ta * reach, tip - axis * 0.3 + bend + side_a * geometry.gap * 0.45, tip),
        (tip, tip - axis * 0.3 + bend - side_a * geometry.gap * 0.45, b - tb * reach, b),
    )


def middle_point(geometry, pose):
    if pose.bend is None:
        return tuple(tail_curve(geometry, pose.root_u, pose.tip).middle() + geometry.guide.origin)
    root = np.asarray(geometry.guide.point_at(pose.root_u)) + geometry.guide.origin
    axis = np.asarray(pose.tip) - root
    length = max(1, float(np.linalg.norm(axis)))
    side = np.array((-axis[1], axis[0])) / length
    return tuple(root + axis * pose.bend[0] + side * pose.bend[1])


def bend_from_point(geometry, pose, point):
    root = np.asarray(geometry.guide.point_at(pose.root_u)) + geometry.guide.origin
    axis = np.asarray(pose.tip) - root
    length = max(1, float(np.linalg.norm(axis)))
    direction = axis / length
    side = np.array((-direction[1], direction[0]))
    relative = np.asarray(point) - root
    return (
        float(np.clip(relative @ direction / length, 0.15, 0.85)),
        float(np.clip(relative @ side, -length * 0.6, length * 0.6)),
    )
