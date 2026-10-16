"""Bounded cubic fitting and fixed-correspondence displacement of raster samples."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from itertools import pairwise

import numpy as np

Point = tuple[float, float]
MAX_POINTS = 32768
MAX_ANCHORS = 512


@dataclass(frozen=True)
class Anchor:
    pos: Point
    incoming: Point
    outgoing: Point


def evaluate(a: Anchor, b: Anchor, t: float) -> Point:
    s = 1.0 - t
    return tuple(  # type: ignore[return-value]
        s**3 * a.pos[i]
        + 3 * s * s * t * a.outgoing[i]
        + 3 * s * t * t * b.incoming[i]
        + t**3 * b.pos[i]
        for i in range(2)
    )


@dataclass(frozen=True)
class Curve:
    points: tuple[Point, ...]
    anchors: tuple[Anchor, ...]
    bindings: tuple[tuple[int, float], ...]

    def project(self, edited: tuple[Anchor, ...]) -> tuple[Point, ...]:
        if len(edited) != len(self.anchors):
            raise ValueError("Recontour topology must remain fixed")
        if edited == self.anchors:
            return self.points
        if len(edited) == 1:
            dx = edited[0].pos[0] - self.anchors[0].pos[0]
            dy = edited[0].pos[1] - self.anchors[0].pos[1]
            return tuple((x + dx, y + dy) for x, y in self.points)
        indices = np.array([binding[0] for binding in self.bindings])
        t = np.array([binding[1] for binding in self.bindings])[:, None]
        s = 1 - t
        old = np.array([[a.pos, a.incoming, a.outgoing] for a in self.anchors])
        new = np.array([[a.pos, a.incoming, a.outgoing] for a in edited])
        delta = new - old
        displacement = (
            s**3 * delta[indices, 0]
            + 3 * s * s * t * delta[indices, 2]
            + 3 * s * t * t * delta[indices + 1, 1]
            + t**3 * delta[indices + 1, 0]
        )
        result = np.asarray(self.points) + displacement
        return tuple((float(x), float(y)) for x, y in result)


def fit(points: tuple[Point, ...], smoothness: float) -> Curve:  # noqa: PLR0912, PLR0915
    """Fit with ordered arc-length parameters, preserving corners and endpoints.

    An iterative split limits work and avoids recursion on pathological strokes.
    Bernstein control bounds constrain deviation over each entire input edge.
    """
    if not points or len(points) > MAX_POINTS:
        raise ValueError("Recontour stroke sample limit exceeded")
    p = np.asarray(points, dtype=float)
    if not np.isfinite(p).all() or not math.isfinite(smoothness):
        raise ValueError("Non-finite Recontour geometry")
    unique = np.r_[True, np.any(np.diff(p, axis=0) != 0, axis=1)]
    if not unique.all():
        reduced = fit(
            tuple(point for point, keep in zip(points, unique, strict=True) if keep), smoothness
        )
        indices = np.cumsum(unique) - 1
        return Curve(points, reduced.anchors, tuple(reduced.bindings[i] for i in indices))
    lengths = np.linalg.norm(np.diff(p, axis=0), axis=1)
    cumulative = np.r_[0.0, np.cumsum(lengths)]
    if cumulative[-1] < 1e-9:
        a = Anchor(points[0], points[0], points[0])
        return Curve(points, (a,), tuple((0, 0.0) for _ in points))
    tolerance = 0.15 + 7.85 * (min(100.0, max(0.0, smoothness)) / 100.0) ** 2
    corners = {0, len(points) - 1}
    edges = np.diff(p, axis=0)
    norms = lengths[:-1] * lengths[1:]
    dots = np.sum(edges[:-1] * edges[1:], axis=1)
    corners.update(int(i + 1) for i in np.flatnonzero((norms > 1e-12) & (dots < norms * 0.5)))
    boundaries = sorted(corners)
    pending = list(pairwise(boundaries))
    segments = []
    work = 0
    while pending:
        if len(segments) + len(pending) >= MAX_ANCHORS:
            raise ValueError("Recontour anchor budget exceeded")
        first, last = pending.pop()
        work += last - first + 1
        if work > 2_000_000:
            raise ValueError("Recontour fitting work budget exceeded")
        arc = cumulative[last] - cumulative[first]
        ts = (cumulative[first : last + 1] - cumulative[first]) / max(arc, 1e-12)
        chord = float(np.linalg.norm(p[last] - p[first]))
        tangents = []
        for index, sign in ((first, 1), (last, -1)):
            if index in corners:
                v = p[min(last, index + 1)] - p[max(first, index - 1)]
            else:
                v = p[min(len(p) - 1, index + 1)] - p[max(0, index - 1)]
            tangents.append(v / max(float(np.linalg.norm(v)), 1e-12) * sign)
        h1 = p[first] + tangents[0] * chord / 3
        h2 = p[last] + tangents[1] * chord / 3
        a = Anchor(tuple(p[first]), tuple(p[first]), tuple(h1))
        b = Anchor(tuple(p[last]), tuple(h2), tuple(p[last]))
        t = ts[:, None]
        s = 1 - t
        predicted = s**3 * p[first] + 3 * s * s * t * h1 + 3 * s * t * t * h2 + t**3 * p[last]
        derivative = (
            3 * s * s * (h1 - p[first]) + 6 * s * t * (h2 - h1) + 3 * t * t * (p[last] - h2)
        )
        delta_t = np.diff(ts)[:, None] / 3
        target = p[first : last + 1]
        edge = np.diff(target, axis=0) / 3
        differences = np.stack(
            (
                predicted[:-1] - target[:-1],
                predicted[:-1] + derivative[:-1] * delta_t - target[:-1] - edge,
                predicted[1:] - derivative[1:] * delta_t - target[1:] + edge,
                predicted[1:] - target[1:],
            )
        )
        errors = np.linalg.norm(differences, axis=2).max(axis=0)
        if last - first == 1 and float(errors.max()) > tolerance:
            a = replace(a, outgoing=tuple(p[first] + (p[last] - p[first]) / 3))
            b = replace(b, incoming=tuple(p[last] - (p[last] - p[first]) / 3))
        if last - first <= 1 or float(errors.max()) <= tolerance:
            segments.append((first, last, a, b, ts))
        else:
            split = int(errors.argmax()) + first + 1
            split = max(first + 1, min(last - 1, split))
            pending.extend(((first, split), (split, last)))
    segments.sort(key=lambda item: item[0])
    anchors = [segments[0][2]]
    bindings = [(0, 0.0)] * len(points)
    for index, (first, _last, a, b, ts) in enumerate(segments):
        anchors[-1] = replace(anchors[-1], outgoing=a.outgoing)
        anchors.append(b)
        for offset, t in enumerate(ts):
            bindings[first + offset] = (index, float(t))
    return Curve(points, tuple(anchors), tuple(bindings))


def move_anchor(
    anchors: tuple[Anchor, ...], index: int, part: str, pos: Point
) -> tuple[Anchor, ...]:
    values = list(anchors)
    anchor = values[index]
    if part == "pos":
        dx, dy = pos[0] - anchor.pos[0], pos[1] - anchor.pos[1]
        values[index] = Anchor(
            pos,
            (anchor.incoming[0] + dx, anchor.incoming[1] + dy),
            (anchor.outgoing[0] + dx, anchor.outgoing[1] + dy),
        )
    elif part in ("incoming", "outgoing"):
        values[index] = replace(anchor, **{part: pos})
    else:
        raise ValueError(part)
    return tuple(values)
