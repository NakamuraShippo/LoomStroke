"""Per-stroke compiled assist: constrain each point, then fan out symmetry copies.

Compiled once at stroke start (no per-point validation / allocation beyond the
returned tuples) and used unchanged for preview and commit (CT-14 §performance).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .concentric_constraint import ConcentricConstraint
from .drawing_assist import DrawingAssist
from .ellipse_constraint import EllipseConstraint
from .mirror_geometry import MirrorGeometry
from .parallel_constraint import ParallelConstraint
from .perspective_constraint import PerspectiveConstraint
from .radial_constraint import RadialConstraint
from .rotational_geometry import RotationalGeometry

__all__ = ["StrokeConstraintSession"]

Point = tuple[float, float]
MAX_COPIES = 16


@dataclass(slots=True)
class StrokeConstraintSession:
    assist_id: str
    assist_type: str
    constraint: Any = None
    mirror: MirrorGeometry | None = None
    rotational: RotationalGeometry | None = None
    include_original: bool = True
    anchor: Point | None = field(default=None)

    @classmethod
    def compile(cls, assist: DrawingAssist | None) -> StrokeConstraintSession | None:
        if assist is None or not assist.enabled or not assist.is_known:
            return None
        builder = getattr(cls, f"_compile_{assist.type}")
        return builder(assist)

    # -- use ---------------------------------------------------------------------------

    def constrain(self, x: float, y: float) -> Point:
        """Constrain one incoming point; the first point of the stroke becomes the anchor."""
        if self.anchor is None:
            self.anchor = (x, y)
            if isinstance(self.constraint, EllipseConstraint) and not self.constraint.concentric:
                return self.constraint.project(self.anchor, x, y)
            return (x, y)
        if self.constraint is None:
            return (x, y)
        return self.constraint.project(self.anchor, x, y)

    def copies(self, x: float, y: float) -> tuple[Point, ...]:
        if self.mirror is not None:
            return (self.mirror.reflect(x, y),)
        if self.rotational is not None:
            return self.rotational.copies(x, y)[:MAX_COPIES]
        return ()

    @property
    def copy_count(self) -> int:
        if self.mirror is not None:
            return 1
        if self.rotational is not None:
            return min(MAX_COPIES, len(self.rotational.copies(0.0, 1.0)))
        return 0

    @property
    def has_copies(self) -> bool:
        return self.copy_count > 0

    def reset(self) -> None:
        self.anchor = None

    # -- compile per type --------------------------------------------------------------

    @classmethod
    def _compile_mirror(cls, assist: DrawingAssist) -> StrokeConstraintSession | None:
        p0, p1 = assist.point("p0"), assist.point("p1")
        if p0 is None or p1 is None:
            return None
        mirror = MirrorGeometry.from_points(p0, p1)
        if mirror is None:
            return None
        return cls(
            assist.id,
            "mirror",
            mirror=mirror,
            include_original=bool(assist.option("include_original", True)),
        )

    @classmethod
    def _compile_rotational(cls, assist: DrawingAssist) -> StrokeConstraintSession | None:
        center = assist.point("center")
        if center is None:
            return None
        count = int(assist.number("count", 2.0, source="options"))
        count = max(2, min(MAX_COPIES + 1, count))
        return cls(
            assist.id,
            "rotational",
            rotational=RotationalGeometry(
                center[0], center[1], count, bool(assist.option("mirror", False))
            ),
            include_original=bool(assist.option("include_original", True)),
        )

    @classmethod
    def _compile_parallel(cls, assist: DrawingAssist) -> StrokeConstraintSession:
        angle = assist.number("angle", 0.0)
        return cls(assist.id, "parallel", constraint=ParallelConstraint.from_angle(angle))

    @classmethod
    def _compile_radial(cls, assist: DrawingAssist) -> StrokeConstraintSession | None:
        center = assist.point("center")
        if center is None:
            return None
        return cls(assist.id, "radial", constraint=RadialConstraint(center[0], center[1]))

    @classmethod
    def _compile_concentric(cls, assist: DrawingAssist) -> StrokeConstraintSession | None:
        center = assist.point("center")
        if center is None:
            return None
        ratio = assist.number("ratio", 1.0, source="options")
        return cls(
            assist.id, "concentric", constraint=ConcentricConstraint(center[0], center[1], ratio)
        )

    @classmethod
    def _compile_perspective(cls, assist: DrawingAssist) -> StrokeConstraintSession | None:
        points = assist.points("vanishing_points")
        if not points:
            return None
        axis = str(assist.option("axis", "auto") or "auto")
        return cls(assist.id, "perspective", constraint=PerspectiveConstraint(points[:3], axis))

    @classmethod
    def _compile_ellipse(cls, assist: DrawingAssist) -> StrokeConstraintSession | None:
        center = assist.point("center")
        major = assist.number("major_diameter", 0.0)
        minor = assist.number("minor_diameter", 0.0)
        if center is None or not 1e-3 <= minor <= major:
            return None
        angle = math.radians(assist.number("angle", 0.0))
        return cls(
            assist.id,
            "ellipse",
            constraint=EllipseConstraint(
                *center,
                major / 2,
                minor / 2,
                math.cos(angle),
                math.sin(angle),
                bool(assist.option("concentric", True)),
            ),
        )
