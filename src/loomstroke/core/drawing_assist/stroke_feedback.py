"""Actual constraint geometry for one live stroke; independent of Qt."""

import math

from .assist_overlay_geometry import AssistOverlayGeometry
from .concentric_constraint import ConcentricConstraint
from .parallel_constraint import ParallelConstraint
from .perspective_constraint import PerspectiveConstraint
from .radial_constraint import RadialConstraint
from .stroke_constraint_session import StrokeConstraintSession


def stroke_feedback(
    session: StrokeConstraintSession, point: tuple[float, float], size: tuple[float, float]
) -> AssistOverlayGeometry:
    anchor, constraint = session.anchor, session.constraint
    if anchor is None:
        return AssistOverlayGeometry()
    markers = ()
    if isinstance(constraint, PerspectiveConstraint):
        constraint = session._perspective_locked or constraint
        if constraint.axis == "auto":
            return AssistOverlayGeometry()
        if constraint.axis == "horizontal":
            target = (anchor[0] + 1, anchor[1])
        elif constraint.axis == "vertical":
            target = (anchor[0], anchor[1] + 1)
        elif constraint.axis in ("vp1", "vp2", "vp3") and constraint.vanishing_points:
            index = max(0, min(int(constraint.axis[2:]) - 1, len(constraint.vanishing_points) - 1))
            target = constraint.vanishing_points[index]
            markers = ((target[0], target[1], f"VP{index + 1}"),)
        else:
            return AssistOverlayGeometry()
    elif isinstance(constraint, ParallelConstraint):
        target = (anchor[0] + constraint.dir_x, anchor[1] + constraint.dir_y)
    elif isinstance(constraint, RadialConstraint):
        target = (constraint.center_x, constraint.center_y)
        if math.dist(anchor, target) < 1e-9:
            target = point
    elif isinstance(constraint, ConcentricConstraint):
        radius = constraint.radius_through(anchor)
        return AssistOverlayGeometry(
            ellipses=(
                (
                    constraint.center_x,
                    constraint.center_y,
                    radius,
                    radius * constraint.ratio,
                ),
            )
        )
    else:
        return AssistOverlayGeometry()
    return _line_through(anchor, target, size, markers)


def _line_through(anchor, target, size, markers):
    dx, dy = target[0] - anchor[0], target[1] - anchor[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return AssistOverlayGeometry(markers=markers)
    reach = max(math.hypot(*size) * 2, length)
    dx, dy = dx / length * reach, dy / length * reach
    return AssistOverlayGeometry(
        lines=(((anchor[0] - dx, anchor[1] - dy), (anchor[0] + dx, anchor[1] + dy)),),
        markers=markers,
    )
