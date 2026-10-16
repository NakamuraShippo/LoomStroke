"""Find retained cubic ranges for area, whole-stroke, and intersection erasing."""

from __future__ import annotations

import math
from dataclasses import replace
from itertools import pairwise
from typing import Any

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath

from .cubic_stroke_segment import CubicStrokeSegment
from .segment_intersector import SegmentIntersector
from .vector_erase_fragment import VectorEraseFragment


class VectorEraseGeometry:
    @classmethod
    def retained(
        cls, path: Any, area: QPainterPath, mode: str, obstacles: list[Any], step: float
    ) -> list[Any] | None:
        anchors = list(path.anchors)
        if len(anchors) < 2:
            return None
        pairs = list(pairwise(anchors))
        if path.closed:
            pairs.append((anchors[-1], anchors[0]))
        segments = [CubicStrokeSegment.from_anchors(a, b) for a, b in pairs]
        samples = cls._samples(segments, step)
        touched = [area.contains(QPointF(*point)) for _, point in samples]
        if not any(touched):
            return None
        if mode == "Whole Stroke":
            return []
        ranges = (
            cls._intersection_ranges(segments, samples, touched, obstacles, step)
            if mode == "To Intersection"
            else cls._area_ranges(segments, samples, touched, area)
        )
        cumulative = [0.0]
        for (_, a), (_, b) in pairwise(samples):
            cumulative.append(cumulative[-1] + math.dist(a, b))
        result = []
        for lower, upper in ranges:
            kept = []
            for index, segment in enumerate(segments):
                a, b = max(0.0, lower - index), min(1.0, upper - index)
                if b - a <= 1e-6:
                    continue
                start, end = segment.slice(a, b).anchors(anchors[index])
                if kept:
                    kept[-1] = replace(kept[-1], h_out=start.h_out)
                else:
                    kept.append(start)
                kept.append(end)
            if len(kept) >= 2:
                result.append(
                    VectorEraseFragment(
                        replace(path, anchors=kept, closed=False),
                        cls._fraction(samples, cumulative, lower),
                        cls._fraction(samples, cumulative, upper),
                    )
                )
        return result

    @staticmethod
    def _samples(segments, step):
        samples = []
        for index, segment in enumerate(segments):
            length = sum(math.dist(a, b) for a, b in pairwise(segment.controls))
            count = max(2, math.ceil(length / max(0.5, step)))
            if count + len(samples) > 100_000:
                raise ValueError("Vector erase sampling limit exceeded")
            samples.extend(
                (index + sample / count, segment.point(sample / count)) for sample in range(count)
            )
        samples.append((float(len(segments)), segments[-1].controls[-1]))
        return samples

    @classmethod
    def _intersection_ranges(cls, segments, samples, touched, obstacles, step):
        cuts = []
        for other in obstacles:
            polygon = cls._polyline(other, step)
            for (t0, a), (t1, b) in pairwise(samples):
                for hit in SegmentIntersector.with_polyline(a, b, polygon):
                    cuts.append(t0 + (t1 - t0) * hit.t)
        cuts = sorted({0.0, float(len(segments)), *cuts})
        return [
            (a, b)
            for a, b in pairwise(cuts)
            if not any(a < t < b and hit for (t, _), hit in zip(samples, touched, strict=True))
        ]

    @classmethod
    def _area_ranges(cls, segments, samples, touched, area):
        boundaries = [0.0]
        for ((t0, _), (t1, _)), (hit0, hit1) in zip(
            pairwise(samples), pairwise(touched), strict=True
        ):
            if hit0 != hit1:
                lower, upper = t0, t1
                for _ in range(14):
                    middle = (lower + upper) / 2
                    if area.contains(QPointF(*cls._point(segments, middle))) == hit0:
                        lower = middle
                    else:
                        upper = middle
                boundaries.append((lower + upper) / 2)
        boundaries.append(float(len(segments)))
        return [
            (a, b)
            for a, b in pairwise(boundaries)
            if not area.contains(QPointF(*cls._point(segments, (a + b) / 2)))
        ]

    @staticmethod
    def _fraction(samples, cumulative, t):
        for index, ((t0, _), (t1, _)) in enumerate(pairwise(samples)):
            if t0 <= t <= t1:
                distance = cumulative[index] + (cumulative[index + 1] - cumulative[index]) * (
                    t - t0
                ) / (t1 - t0)
                return distance / max(1e-12, cumulative[-1])
        return 1.0

    @staticmethod
    def _point(segments: list[CubicStrokeSegment], t: float) -> tuple[float, float]:
        index = min(len(segments) - 1, int(t))
        return segments[index].point(t - index)

    @staticmethod
    def _polyline(path: Any, step: float) -> list[tuple[float, float]]:
        points = []
        anchors = list(path.anchors)
        pairs = list(pairwise(anchors))
        if path.closed and anchors:
            pairs.append((anchors[-1], anchors[0]))
        for a, b in pairs:
            segment = CubicStrokeSegment.from_anchors(a, b)
            length = sum(math.dist(p, q) for p, q in pairwise(segment.controls))
            count = min(2048, max(2, math.ceil(length / max(0.5, step))))
            points.extend(segment.point(i / count) for i in range(count))
        if pairs:
            points.append(pairs[-1][1].pos)
        return points
