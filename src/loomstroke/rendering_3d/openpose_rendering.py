"""CMU OpenPose palettes/topology and CPU renderer's destination-color rule.

References: include/openpose/{hand/handParameters.hpp,face/faceParameters.hpp}
and src/openpose/utilities/keypoint.cpp::renderKeypointsCpu in CMU OpenPose.
Qt rasterization is used; this is not a pixel-identical OpenCV renderer.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

HAND_COLORS = (
    (100, 100, 100),
    (100, 0, 0),
    (150, 0, 0),
    (200, 0, 0),
    (255, 0, 0),
    (100, 100, 0),
    (150, 150, 0),
    (200, 200, 0),
    (255, 255, 0),
    (0, 100, 50),
    (0, 150, 75),
    (0, 200, 100),
    (0, 255, 125),
    (0, 50, 100),
    (0, 75, 150),
    (0, 100, 200),
    (0, 125, 255),
    (100, 0, 100),
    (150, 0, 150),
    (200, 0, 200),
    (255, 0, 255),
)


def _chain(start: int, end: int, *, closed: bool = False) -> tuple[tuple[int, int], ...]:
    edges = tuple((index, index + 1) for index in range(start, end))
    return (*edges, (end, start)) if closed else edges


HAND_EDGES = tuple(
    edge for start in (1, 5, 9, 13, 17) for edge in ((0, start), *_chain(start, start + 3))
)
FACE_EDGES = (
    *_chain(0, 16),
    *_chain(17, 21),
    *_chain(22, 26),
    *_chain(27, 30),
    *_chain(31, 35),
    *_chain(36, 41, closed=True),
    *_chain(42, 47, closed=True),
    *_chain(48, 59, closed=True),
    *_chain(60, 67, closed=True),
)


def draw_keypoints(
    painter: QPainter,
    points: np.ndarray,
    edges: tuple[tuple[int, int], ...],
    colors: tuple[tuple[int, int, int], ...],
    *,
    line_width: float,
    radius: float,
) -> None:
    """Draw official connections with endpoint-B color, then each joint's color."""
    for a, b in edges:
        if max(a, b) < len(points) and points[a, 2] > 0 and points[b, 2] > 0:
            painter.setPen(
                QPen(
                    QColor(*colors[b % len(colors)]),
                    max(1, int(line_width + 0.5)),
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawLine(QPointF(*points[a, :2]), QPointF(*points[b, :2]))
    painter.setPen(Qt.PenStyle.NoPen)
    for index, point in enumerate(points):
        if point[2] > 0:
            painter.setBrush(QColor(*colors[index % len(colors)]))
            painter.drawEllipse(QPointF(*point[:2]), radius, radius)
