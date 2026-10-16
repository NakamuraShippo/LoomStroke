"""CPU fill-geometry compilation for direct vector rendering (spec §8.1).

Converts ``VectorObjectSnapshot`` paths into triangle geometry that the WGPU
vector renderer can upload.  Pure CPU / Qt-free so it can run on worker
threads.  The flattening rules mirror the current QPainter path builder
(``core.vector_text_render_primitives``): relative handles become cubic
segments, handles below the 1e-3 threshold degrade to straight lines, and a
subpath counts as closed when flagged or when its endpoints are within 0.5px.

Fill parity note: ``build_vector_qpaths`` creates one ``QPainterPath`` per
``VectorPath`` and fills each independently, so subpaths of one object never
form compound holes in the current renderer.  This module therefore fills
each subpath on its own (open subpaths implicitly closed, matching QPainter
brush fill).  Even-odd only applies within a single self-intersecting
subpath, which earcut approximates without crashing.
"""

from __future__ import annotations

import math
from array import array
from collections.abc import Sequence
from dataclasses import dataclass

from loomstroke.core.vector_render_models import (
    VectorObjectSnapshot,
    VectorPathSnapshot,
)
from loomstroke.ui.canvas.wgpu_backend.vector.earcut import earcut

# Matches _MAX_VECTOR_COORD_ABS in plugins.vector_tools.common.
_MAX_COORD_ABS = 1_000_000.0
_HANDLE_EPSILON = 1e-3
_AUTO_CLOSE_DISTANCE_SQ = 0.25
_DEFAULT_TOLERANCE = 0.25
_MAX_SUBDIVISION_DEPTH = 16

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class CompiledFillGeometry:
    """Triangulated fill geometry in document space (spec §8.1)."""

    vertices: bytes  # float32 interleaved [x, y] * vertex_count
    indices: bytes  # uint32 * index_count
    vertex_count: int
    index_count: int
    bounds: tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y)
    lod_bucket: int = 0

    @property
    def is_empty(self) -> bool:
        return self.index_count == 0


def tessellate_fill(
    obj: VectorObjectSnapshot,
    *,
    tolerance: float = _DEFAULT_TOLERANCE,
    lod_bucket: int = 0,
) -> CompiledFillGeometry | None:
    """Compile the fill geometry of *obj*, or return ``None`` on fallback.

    ``None`` means the object cannot be represented (no fill enabled, or
    malformed coordinates) and the caller must route the object to the
    compatibility raster path.  Objects that are valid but cover no area
    return an empty geometry, which is a normal no-draw.
    """

    if not obj.style.fill_enabled or obj.style.fill_color is None:
        return None

    rings: list[list[Point]] = []
    for path in obj.paths:
        flattened = flatten_subpath(path, tolerance)
        if flattened is None:
            return None
        points, _closed = flattened
        ring = _dedupe_ring(points)
        if len(ring) >= 3:
            rings.append(ring)

    if not rings:
        return _empty_geometry(lod_bucket)

    vertices = array("f")
    indices = array("I")
    for ring in rings:
        _triangulate_ring(ring, vertices, indices)

    if not indices:
        return _empty_geometry(lod_bucket)

    bounds = _bounds_of_rings(rings)
    return CompiledFillGeometry(
        vertices=vertices.tobytes(),
        indices=indices.tobytes(),
        vertex_count=len(vertices) // 2,
        index_count=len(indices),
        bounds=bounds,
        lod_bucket=lod_bucket,
    )


def _empty_geometry(lod_bucket: int) -> CompiledFillGeometry:
    return CompiledFillGeometry(
        vertices=b"",
        indices=b"",
        vertex_count=0,
        index_count=0,
        bounds=(0.0, 0.0, 0.0, 0.0),
        lod_bucket=lod_bucket,
    )


def flatten_subpath(
    path: VectorPathSnapshot,
    tolerance: float = _DEFAULT_TOLERANCE,
) -> tuple[list[Point], bool] | None:
    """Flatten one subpath into ``(points, closed)``.

    ``closed`` follows the QPainter builder rule (explicit flag or endpoints
    within 0.5px); for closed subpaths the flattened closing segment is
    included (without duplicating the start point).  Returns ``None`` when
    coordinates are malformed (NaN/inf/out of range).  Fill callers treat the
    point list as a polygon ring (implicit straight close for open subpaths,
    matching Qt brush fill); stroke callers use ``closed`` to pick caps
    versus a wrap-around join.
    """

    anchors = path.anchors
    if len(anchors) < 2:
        if any(not _valid_point(anchor.pos) for anchor in anchors):
            return None
        return ([(float(a.pos[0]), float(a.pos[1])) for a in anchors], False)

    for anchor in anchors:
        if (
            not _valid_point(anchor.pos)
            or (anchor.h_in is not None and not _valid_point(anchor.h_in))
            or (anchor.h_out is not None and not _valid_point(anchor.h_out))
        ):
            return None

    points: list[Point] = [(float(anchors[0].pos[0]), float(anchors[0].pos[1]))]
    for index in range(len(anchors) - 1):
        _append_segment(points, anchors[index], anchors[index + 1], tolerance)

    closed = _segment_is_closed(path)
    if closed:
        # Closing segment may curve, mirroring _close_qpath.
        _append_segment(points, anchors[-1], anchors[0], tolerance)
        if len(points) > 1 and points[0] == points[-1]:
            points.pop()

    return (points, closed)


def _segment_is_closed(path: VectorPathSnapshot) -> bool:
    if path.closed:
        return True
    if len(path.anchors) < 2:
        return False
    first = path.anchors[0].pos
    last = path.anchors[-1].pos
    return (first[0] - last[0]) ** 2 + (first[1] - last[1]) ** 2 < _AUTO_CLOSE_DISTANCE_SQ


def _append_segment(points: list[Point], anchor: object, next_anchor: object, tol: float) -> None:
    pos = anchor.pos  # type: ignore[attr-defined]
    next_pos = next_anchor.pos  # type: ignore[attr-defined]
    h_out = anchor.h_out  # type: ignore[attr-defined]
    h_in = next_anchor.h_in  # type: ignore[attr-defined]
    has_out = h_out is not None and (
        abs(h_out[0]) > _HANDLE_EPSILON or abs(h_out[1]) > _HANDLE_EPSILON
    )
    has_in = h_in is not None and (abs(h_in[0]) > _HANDLE_EPSILON or abs(h_in[1]) > _HANDLE_EPSILON)
    p0 = (float(pos[0]), float(pos[1]))
    p3 = (float(next_pos[0]), float(next_pos[1]))
    if not has_out and not has_in:
        points.append(p3)
        return
    c1 = (p0[0] + (h_out[0] if h_out else 0.0), p0[1] + (h_out[1] if h_out else 0.0))
    c2 = (p3[0] + (h_in[0] if h_in else 0.0), p3[1] + (h_in[1] if h_in else 0.0))
    _flatten_cubic(points, p0, c1, c2, p3, tol, 0)
    points.append(p3)


def _flatten_cubic(
    points: list[Point],
    p0: Point,
    c1: Point,
    c2: Point,
    p3: Point,
    tol: float,
    depth: int,
) -> None:
    """Adaptive de Casteljau subdivision; appends interior points only."""

    if depth >= _MAX_SUBDIVISION_DEPTH or _cubic_is_flat(p0, c1, c2, p3, tol):
        return

    p01 = _midpoint(p0, c1)
    p12 = _midpoint(c1, c2)
    p23 = _midpoint(c2, p3)
    p012 = _midpoint(p01, p12)
    p123 = _midpoint(p12, p23)
    mid = _midpoint(p012, p123)

    _flatten_cubic(points, p0, p01, p012, mid, tol, depth + 1)
    points.append(mid)
    _flatten_cubic(points, mid, p123, p23, p3, tol, depth + 1)


def _cubic_is_flat(p0: Point, c1: Point, c2: Point, p3: Point, tol: float) -> bool:
    # Max distance of control points from the chord (Roger Willcocks metric).
    ux = 3.0 * c1[0] - 2.0 * p0[0] - p3[0]
    uy = 3.0 * c1[1] - 2.0 * p0[1] - p3[1]
    vx = 3.0 * c2[0] - p0[0] - 2.0 * p3[0]
    vy = 3.0 * c2[1] - p0[1] - 2.0 * p3[1]
    return max(ux * ux, vx * vx) + max(uy * uy, vy * vy) <= 16.0 * tol * tol


def _midpoint(a: Point, b: Point) -> Point:
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)


def _dedupe_ring(points: list[Point]) -> list[Point]:
    result: list[Point] = []
    for point in points:
        if not result or point != result[-1]:
            result.append(point)
    if len(result) > 1 and result[0] == result[-1]:
        result.pop()
    return result


def _triangulate_ring(
    ring: list[Point],
    vertices: array[float],
    indices: array[int],
) -> None:
    data: list[float] = []
    for x, y in ring:
        data.extend((x, y))

    triangles = earcut(data)
    if not triangles:
        return

    base = len(vertices) // 2
    vertices.extend(data)
    indices.extend(base + index for index in triangles)


def _bounds_of_rings(rings: Sequence[Sequence[Point]]) -> tuple[float, float, float, float]:
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    for ring in rings:
        for x, y in ring:
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
    if min_x > max_x:
        return (0.0, 0.0, 0.0, 0.0)
    return (min_x, min_y, max_x, max_y)


def _valid_point(point: Sequence[float]) -> bool:
    try:
        x = float(point[0])
        y = float(point[1])
    except (TypeError, ValueError, IndexError):
        return False
    return (
        math.isfinite(x)
        and math.isfinite(y)
        and abs(x) <= _MAX_COORD_ABS
        and abs(y) <= _MAX_COORD_ABS
    )
