"""Readable object rotation along a captured ruler direction, without Qt."""

import math

from .concentric_constraint import ConcentricConstraint
from .parallel_constraint import ParallelConstraint
from .perspective_constraint import PerspectiveConstraint
from .radial_constraint import RadialConstraint


def object_direction(session, *, vertical=False):
    if session is None or session.anchor is None or session.constraint is None:
        return 0.0
    x, y = session.anchor
    constraint = session._perspective_locked or session.constraint
    if isinstance(constraint, ParallelConstraint):
        dx, dy = constraint.dir_x, constraint.dir_y
    elif isinstance(constraint, RadialConstraint):
        dx, dy = constraint.center_x - x, constraint.center_y - y
    elif isinstance(constraint, ConcentricConstraint):
        dx, dy = (
            -(y - constraint.center_y) / max(constraint.ratio**2, 1e-12),
            x - constraint.center_x,
        )
    elif isinstance(constraint, PerspectiveConstraint):
        if constraint.axis == "auto":
            return 0.0
        if constraint.axis == "vertical":
            dx, dy = 0, 1
        else:
            index = int(constraint.axis[-1]) - 1
            vx, vy = constraint.vanishing_points[index]
            dx, dy = vx - x, vy - y
    else:
        return 0.0
    if math.hypot(dx, dy) < 1e-9:
        return 0.0
    angle = math.degrees(math.atan2(dy, dx)) - (90 if vertical else 0)
    return (angle + 90) % 180 - 90
