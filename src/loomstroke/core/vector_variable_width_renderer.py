"""Variable-width stroke rendering for the QPainter vector path (CT-15 Phase 1).

Objects whose ``loomstroke:stroke_profile`` carries a variable width profile
are drawn as a filled outline (built by the backend-neutral
``VariableWidthOutlineBuilder``) instead of a constant-width pen.  Outlines
are cached per object geometry / profile signature so pan / zoom never
rebuilds them.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Sequence
from itertools import pairwise
from typing import Any, ClassVar

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QPainter, QPainterPath, QPen, QPolygonF

from .vector_correction import ArcLengthTable, StrokeProfile, VariableWidthOutlineBuilder
from .vector_render_models import stable_signature

__all__ = ["VECTOR_STROKE_PROFILE_META_KEY", "VariableWidthPathRenderer"]

VECTOR_STROKE_PROFILE_META_KEY = "loomstroke:stroke_profile"


class VariableWidthPathRenderer:
    CACHE_SIZE: ClassVar[int] = 256
    FLATTEN_TOLERANCE: ClassVar[float] = 0.25

    def __init__(self) -> None:
        self._cache: OrderedDict[tuple[str, int], QPainterPath] = OrderedDict()

    # -- queries -----------------------------------------------------------------------

    @staticmethod
    def profile_for(vec: Any) -> StrokeProfile | None:
        meta = getattr(vec, "meta", None)
        if not isinstance(meta, dict):
            return None
        raw = meta.get(VECTOR_STROKE_PROFILE_META_KEY)
        if raw is None:
            return None
        style = getattr(vec, "style", None)
        base = float(getattr(style, "stroke_width", 1.0) or 1.0)
        profile = StrokeProfile.from_raw(raw, base)
        if profile is None or not profile.is_variable:
            return None
        return profile

    # -- drawing -----------------------------------------------------------------------

    def draw(
        self,
        painter: QPainter,
        vec: Any,
        qpaths: Sequence[QPainterPath],
        pen: QPen,
        brush: QBrush | Qt.BrushStyle,
        profile: StrokeProfile,
    ) -> None:
        if not isinstance(brush, Qt.BrushStyle):
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(brush)
            for qpath in qpaths:
                if not qpath.isEmpty():
                    painter.drawPath(qpath)
            painter.restore()
        outline = self.outline_path(vec, qpaths, profile)
        if outline.isEmpty():
            return
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(pen.color()))
        painter.drawPath(outline)
        painter.restore()

    def outline_path(
        self, vec: Any, qpaths: Sequence[QPainterPath], profile: StrokeProfile
    ) -> QPainterPath:
        key = (str(getattr(vec, "id", "") or ""), self._signature(vec, profile))
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        outline = self._build(vec, qpaths, profile)
        self._cache[key] = outline
        while len(self._cache) > self.CACHE_SIZE:
            self._cache.popitem(last=False)
        return outline

    # -- internals ---------------------------------------------------------------------

    def _build(
        self, vec: Any, qpaths: Sequence[QPainterPath], profile: StrokeProfile
    ) -> QPainterPath:
        style = getattr(vec, "style", None)
        builder = VariableWidthOutlineBuilder(
            miter_limit=float(getattr(style, "miter_limit", 4.0) or 4.0),
            round_caps=str(getattr(style, "cap", "round") or "round") not in {"butt", "square"},
        )
        outline = QPainterPath()
        outline.setFillRule(Qt.FillRule.OddEvenFill)
        paths = list(getattr(vec, "paths", None) or [])
        for index, qpath in enumerate(qpaths):
            if qpath.isEmpty():
                continue
            closed = bool(getattr(paths[index], "closed", False)) if index < len(paths) else False
            for polygon in qpath.toSubpathPolygons():
                points = [(p.x(), p.y()) for p in polygon]
                if closed and len(points) > 2 and points[0] == points[-1]:
                    points = points[:-1]
                points, widths = self._sample_profile(points, profile, closed=closed)
                for ring in builder.build(points, widths, closed=closed):
                    if len(ring) >= 3:
                        outline.addPolygon(QPolygonF([QPointF(x, y) for x, y in ring]))
                        outline.closeSubpath()
        return outline

    @staticmethod
    def _sample_profile(
        points: list[tuple[float, float]], profile: StrokeProfile, *, closed: bool
    ) -> tuple[list[tuple[float, float]], list[float]]:
        """Include profile knots even when a flattened segment has only its endpoints."""
        table = ArcLengthTable.build(points, closed=closed)
        if len(points) < 2 or table.total <= 1e-12:
            return points, [profile.width_at(0.0)] * len(points)
        knots = [t for t, _scale in profile.curve]
        if profile.interpolation == "monotone":
            knots.extend(
                t0 + (t1 - t0) * step / 8
                for (t0, _), (t1, _) in pairwise(profile.curve)
                for step in range(1, 8)
            )
        pending = iter(sorted(set(knots)))
        knot = next(pending, None)
        chain = [*points, points[0]] if closed else points
        sampled = [points[0]]
        widths = [profile.width_at(0.0)]
        for index, (start, end) in enumerate(pairwise(chain)):
            t0, t1 = table.t_at(index), table.t_at(index + 1)
            while knot is not None and knot < t1:
                if knot > t0 and t1 - t0 > 1e-12:
                    ratio = (knot - t0) / (t1 - t0)
                    sampled.append(
                        (
                            start[0] + (end[0] - start[0]) * ratio,
                            start[1] + (end[1] - start[1]) * ratio,
                        )
                    )
                    widths.append(profile.width_at(knot))
                knot = next(pending, None)
            if not closed or index < len(chain) - 2:
                sampled.append(end)
                widths.append(profile.width_at(t1))
        return sampled, widths

    @staticmethod
    def _signature(vec: Any, profile: StrokeProfile) -> int:
        anchors: list[tuple[Any, ...]] = []
        for path in getattr(vec, "paths", None) or []:
            for anchor in getattr(path, "anchors", None) or []:
                anchors.append(
                    (
                        tuple(anchor.pos),
                        tuple(anchor.h_in) if anchor.h_in else None,
                        tuple(anchor.h_out) if anchor.h_out else None,
                    )
                )
            anchors.append(("closed", bool(getattr(path, "closed", False))))
        style = getattr(vec, "style", None)
        return stable_signature(
            (
                anchors,
                profile.to_raw(),
                float(getattr(style, "stroke_width", 1.0) or 1.0),
                str(getattr(style, "cap", "")),
                float(getattr(style, "miter_limit", 4.0) or 4.0),
            )
        )
