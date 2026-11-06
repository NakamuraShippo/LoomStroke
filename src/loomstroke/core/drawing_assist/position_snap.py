"""Pure proximity snapping to displayed assist lines and elliptical rings."""

import math

from loomstroke.core.composition_snap import nearest_line_hit

from .assist_overlay_geometry import AssistOverlayGeometry
from .concentric_constraint import ConcentricConstraint


def snap_to_geometry(geometry, point, radius):
    hit = nearest_line_hit(point, geometry.lines, radius)
    best = None
    if hit is not None:
        candidate, index = hit
        line = geometry.lines[index]
        dx, dy = line[1][0] - line[0][0], line[1][1] - line[0][1]
        length = max(math.hypot(dx, dy), 1e-9)
        markers = tuple(
            marker
            for marker in geometry.markers
            if abs((marker[0] - line[0][0]) * dy - (marker[1] - line[0][1]) * dx) / length < 1e-6
        )
        best = (
            math.dist(point, candidate),
            candidate,
            AssistOverlayGeometry(lines=(line,), markers=markers),
        )
    for cx, cy, rx, ry in geometry.ellipses:
        if rx <= 0 or ry <= 0:
            continue
        candidate = ConcentricConstraint(cx, cy, ry / rx).project((cx + rx, cy), *point)
        distance = math.dist(point, candidate)
        if distance <= radius and (best is None or distance < best[0]):
            best = (distance, candidate, AssistOverlayGeometry(ellipses=((cx, cy, rx, ry),)))
    return (point, AssistOverlayGeometry()) if best is None else (best[1], best[2])
