"""Offset a polyline by a signed distance along averaged vertex normals (miter clamped)."""

from __future__ import annotations

import math
from collections.abc import Sequence

from .path_correction_result import PathCorrectionResult
from .variable_width_outline_builder import VariableWidthOutlineBuilder

__all__ = ["PathOffsetter"]

Point = tuple[float, float]


class PathOffsetter:
    def __init__(self, *, miter_limit: float = 4.0) -> None:
        self._miter_limit = miter_limit

    def offset(
        self, points: Sequence[Point], distance: float, *, closed: bool = False
    ) -> PathCorrectionResult:
        count = len(points)
        if count < 2 or not math.isfinite(distance):
            return PathCorrectionResult(tuple(points), tuple(range(count)), ("path_too_short",))
        if abs(distance) < 1e-9:
            return PathCorrectionResult(tuple(points), tuple(range(count)))
        builder = VariableWidthOutlineBuilder(miter_limit=self._miter_limit, round_caps=False)
        pts = builder._dedupe(points)  # noqa: SLF001 - shared geometry helpers
        if len(pts) < 2:
            return PathCorrectionResult(tuple(points), tuple(range(count)), ("path_too_short",))
        normals = builder._vertex_normals(pts, closed)  # noqa: SLF001
        moved = tuple(
            (p[0] + n[0] * distance, p[1] + n[1] * distance)
            for p, n in zip(pts, normals, strict=True)
        )
        kept = tuple(range(len(moved))) if len(moved) == count else ()
        return PathCorrectionResult(moved, kept)
