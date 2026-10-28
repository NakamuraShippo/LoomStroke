"""Rejects degenerate / non-finite / absurd assist geometry with stable codes (CT-14)."""

from __future__ import annotations

import math
from typing import ClassVar

from .assist_issue import AssistIssue
from .drawing_assist import DrawingAssist

__all__ = ["DrawingAssistValidator"]


class DrawingAssistValidator:
    MAX_COPIES: ClassVar[int] = 16
    MIN_COPIES: ClassVar[int] = 2
    FAR_FACTOR: ClassVar[float] = 10.0
    MIN_AXIS_LENGTH: ClassVar[float] = 1e-3

    def validate(
        self, assist: DrawingAssist, canvas_bounds: tuple[float, float]
    ) -> tuple[AssistIssue, ...]:
        if not assist.is_known:
            return (AssistIssue("assist_type_unknown", "type", assist.type),)
        handler = getattr(self, f"_validate_{assist.type}")
        return tuple(handler(assist, canvas_bounds))

    # -- per type ----------------------------------------------------------------------

    def _validate_mirror(self, assist: DrawingAssist, bounds: tuple[float, float]) -> list:
        issues = self._require_points(assist, ("p0", "p1"), bounds)
        p0, p1 = assist.point("p0"), assist.point("p1")
        if p0 is not None and p1 is not None and math.dist(p0, p1) < self.MIN_AXIS_LENGTH:
            issues.append(AssistIssue("assist_axis_zero_length", "p1"))
        return issues

    def _validate_rotational(self, assist: DrawingAssist, bounds: tuple[float, float]) -> list:
        issues = self._require_points(assist, ("center",), bounds)
        count = assist.number("count", 0.0, source="options")
        if not float(count).is_integer() or not self.MIN_COPIES <= count <= self.MAX_COPIES:
            issues.append(AssistIssue("assist_copy_count_out_of_range", "count"))
        return issues

    def _validate_parallel(self, assist: DrawingAssist, bounds: tuple[float, float]) -> list:
        raw = assist.geometry.get("angle")
        try:
            angle = float(raw)
        except (TypeError, ValueError):
            return [AssistIssue("assist_angle_invalid", "angle")]
        return [] if math.isfinite(angle) else [AssistIssue("assist_angle_invalid", "angle")]

    def _validate_radial(self, assist: DrawingAssist, bounds: tuple[float, float]) -> list:
        return self._require_points(assist, ("center",), bounds)

    def _validate_concentric(self, assist: DrawingAssist, bounds: tuple[float, float]) -> list:
        issues = self._require_points(assist, ("center",), bounds)
        ratio = assist.number("ratio", 1.0, source="options")
        if not 0.05 <= ratio <= 20.0:
            issues.append(AssistIssue("assist_ratio_out_of_range", "ratio"))
        return issues

    def _validate_perspective(self, assist: DrawingAssist, bounds: tuple[float, float]) -> list:
        points = assist.points("vanishing_points")
        raw = assist.geometry.get("vanishing_points")
        if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= 3:
            return [AssistIssue("assist_vanishing_point_count", "vanishing_points")]
        if len(points) != len(raw):
            return [AssistIssue("assist_point_invalid", "vanishing_points")]
        issues: list[AssistIssue] = []
        for point in points:
            if self._too_far(point, bounds):
                issues.append(AssistIssue("assist_point_too_far", "vanishing_points"))
                break
        return issues

    # -- helpers -----------------------------------------------------------------------

    def _require_points(
        self, assist: DrawingAssist, keys: tuple[str, ...], bounds: tuple[float, float]
    ) -> list:
        issues: list[AssistIssue] = []
        for key in keys:
            point = assist.point(key)
            if point is None:
                issues.append(AssistIssue("assist_point_invalid", key))
            elif self._too_far(point, bounds):
                issues.append(AssistIssue("assist_point_too_far", key))
        return issues

    def _too_far(self, point: tuple[float, float], bounds: tuple[float, float]) -> bool:
        width, height = max(1.0, float(bounds[0])), max(1.0, float(bounds[1]))
        limit = max(width, height) * self.FAR_FACTOR
        return abs(point[0]) > limit or abs(point[1]) > limit
