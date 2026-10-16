"""Convex outer contours and their existing boundary provenance, without Qt UI."""

import math

from .models import PanelFrameError


def cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def convex_hull(points):
    points = sorted(set(points))
    halves = []
    for ordered in (points, points[::-1]):
        half = []
        for point in ordered:
            while len(half) >= 2 and cross(half[-2], half[-1], point) <= 1e-8:
                half.pop()
            half.append(point)
        halves.append(half[:-1])
    return tuple(halves[0] + halves[1])


def outer_boundaries(points, shapes):
    candidates = [
        (a, b, ref)
        for vertices, refs in shapes
        for a, b, ref in zip(vertices, vertices[1:] + vertices[:1], refs, strict=True)
    ]
    result = []
    for a, b in zip(points, points[1:] + points[:1], strict=True):
        matching = [
            ref
            for c, d, ref in candidates
            if max(abs(cross(c, d, p)) for p in (a, b)) / math.dist(c, d) < 1e-7
        ]
        if not matching:
            raise PanelFrameError("merged region has no convex boundary provenance")
        result.append(matching[0])
    return tuple(result)
