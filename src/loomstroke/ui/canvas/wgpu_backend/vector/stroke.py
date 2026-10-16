"""CPU stroke-geometry compilation for direct vector rendering (spec §8.2).

Expands stroked paths into triangle geometry.  Pure CPU / Qt-free so it can
run on worker threads.  Semantics mirror the current QPainter pen setup
(``vector_style_paint`` in ``core.vector_text_render_primitives``):

- effective width is ``max(1, int(stroke_width))`` (Qt integer pen width)
- cap: ``butt``/``square`` map directly, anything else renders round
- join: ``miter``/``bevel`` map directly, anything else renders round
- miter limit follows Qt semantics (tip distance from the join point in
  units of the pen width); joins over the limit fall back to bevel
- a dash pattern with two or more entries applies, with entries in units of
  the pen width (Qt ``setDashPattern`` contract)

Each subpath is stroked independently (one QPainterPath per ``VectorPath``
in the current renderer); overlaps are invisible because the stroke color
is constant per object.
"""

from __future__ import annotations

import itertools
import math
from array import array

from loomstroke.core.vector_render_models import VectorObjectSnapshot
from loomstroke.ui.canvas.wgpu_backend.vector.tessellator import (
    CompiledFillGeometry,
    Point,
    flatten_subpath,
)

_DEFAULT_TOLERANCE = 0.25
_MIN_SEGMENT_LENGTH = 1e-9
_MAX_ROUND_STEPS = 64


def tessellate_stroke(
    obj: VectorObjectSnapshot,
    *,
    tolerance: float = _DEFAULT_TOLERANCE,
    lod_bucket: int = 0,
) -> CompiledFillGeometry | None:
    """Compile the stroke geometry of *obj*, or return ``None`` on fallback.

    ``None`` means malformed input (coordinates or width); the caller must
    route the object to the compatibility raster path.  Valid input that
    produces no visible stroke returns an empty geometry (normal no-draw).
    """

    style = obj.style
    if not math.isfinite(style.stroke_width):
        return None
    width = float(max(1, int(style.stroke_width)))
    half = width / 2.0

    builder = _MeshBuilder()
    for path in obj.paths:
        flattened = flatten_subpath(path, tolerance)
        if flattened is None:
            return None
        points, closed = flattened
        polylines = _polylines_for_stroke(points, closed, style.dash, width)
        for polyline, polyline_closed in polylines:
            _stroke_polyline(
                builder,
                polyline,
                closed=polyline_closed,
                half=half,
                width=width,
                join=str(style.join),
                cap=str(style.cap),
                miter_limit=float(style.miter_limit),
                tolerance=tolerance,
            )

    return builder.build(lod_bucket)


class _MeshBuilder:
    """Accumulates triangles as interleaved float32 vertices + uint32 indices."""

    def __init__(self) -> None:
        self.vertices: array[float] = array("f")
        self.indices: array[int] = array("I")
        self.min_x = math.inf
        self.min_y = math.inf
        self.max_x = -math.inf
        self.max_y = -math.inf

    def add_point(self, point: Point) -> int:
        index = len(self.vertices) // 2
        x, y = point
        self.vertices.extend((x, y))
        self.min_x = min(self.min_x, x)
        self.min_y = min(self.min_y, y)
        self.max_x = max(self.max_x, x)
        self.max_y = max(self.max_y, y)
        return index

    def add_triangle(self, a: Point, b: Point, c: Point) -> None:
        self.indices.extend((self.add_point(a), self.add_point(b), self.add_point(c)))

    def add_quad(self, a: Point, b: Point, c: Point, d: Point) -> None:
        """Add quad with vertices in strip order (a-b on one end, c-d on the other)."""

        ia = self.add_point(a)
        ib = self.add_point(b)
        ic = self.add_point(c)
        id_ = self.add_point(d)
        self.indices.extend((ia, ic, ib, ib, ic, id_))

    def add_fan(self, center: Point, radius: float, start_angle: float, sweep: float) -> None:
        """Add a triangle fan approximating an arc around *center*."""

        steps = _arc_steps(radius, abs(sweep))
        center_index = self.add_point(center)
        previous: int | None = None
        for step in range(steps + 1):
            angle = start_angle + sweep * (step / steps)
            index = self.add_point(
                (center[0] + math.cos(angle) * radius, center[1] + math.sin(angle) * radius)
            )
            if previous is not None:
                self.indices.extend((center_index, previous, index))
            previous = index

    def build(self, lod_bucket: int) -> CompiledFillGeometry:
        if not self.indices:
            return CompiledFillGeometry(
                vertices=b"",
                indices=b"",
                vertex_count=0,
                index_count=0,
                bounds=(0.0, 0.0, 0.0, 0.0),
                lod_bucket=lod_bucket,
            )
        return CompiledFillGeometry(
            vertices=self.vertices.tobytes(),
            indices=self.indices.tobytes(),
            vertex_count=len(self.vertices) // 2,
            index_count=len(self.indices),
            bounds=(self.min_x, self.min_y, self.max_x, self.max_y),
            lod_bucket=lod_bucket,
        )


def _arc_steps(radius: float, sweep: float) -> int:
    if radius <= 0.0 or sweep <= 0.0:
        return 1
    # Chord sagitta below ~0.02px keeps round joins/caps close to the
    # antialiased QPainter circles even at small pen widths.
    max_step = 2.0 * math.acos(max(0.0, min(1.0, 1.0 - 0.02 / radius)))
    if max_step <= 0.0:
        return _MAX_ROUND_STEPS
    return max(1, min(_MAX_ROUND_STEPS, math.ceil(sweep / max_step)))


def _polylines_for_stroke(
    points: list[Point],
    closed: bool,
    dash: tuple[float, ...],
    width: float,
) -> list[tuple[list[Point], bool]]:
    cleaned = _dedupe_polyline(points)
    if not cleaned:
        return []
    walk = [*cleaned, cleaned[0]] if closed and len(cleaned) >= 2 else cleaned

    pattern = [value * width for value in dash if math.isfinite(value) and value >= 0.0]
    if len(pattern) < 2 or sum(pattern) <= 0.0:
        return [(cleaned, closed and len(cleaned) >= 3)]
    return [(piece, False) for piece in _dash_polyline(walk, pattern)]


def _dedupe_polyline(points: list[Point]) -> list[Point]:
    result: list[Point] = []
    for point in points:
        if not result or point != result[-1]:
            result.append(point)
    return result


def _dash_polyline(points: list[Point], pattern: list[float]) -> list[list[Point]]:
    """Split *points* into "on" pieces following the dash *pattern* (in px)."""

    pieces: list[list[Point]] = []
    current: list[Point] = []
    pattern_index = 0
    remaining = pattern[0]
    on = True

    def flush() -> None:
        nonlocal current
        if len(current) >= 2:
            pieces.append(current)
        current = []

    for start, end in itertools.pairwise(points):
        seg_len = math.hypot(end[0] - start[0], end[1] - start[1])
        if seg_len <= _MIN_SEGMENT_LENGTH:
            continue
        direction = ((end[0] - start[0]) / seg_len, (end[1] - start[1]) / seg_len)
        travelled = 0.0
        position = start
        if on and not current:
            current = [position]
        while seg_len - travelled > _MIN_SEGMENT_LENGTH:
            step = min(remaining, seg_len - travelled)
            travelled += step
            remaining -= step
            position = (
                start[0] + direction[0] * travelled,
                start[1] + direction[1] * travelled,
            )
            if remaining <= _MIN_SEGMENT_LENGTH:
                if on:
                    current.append(position)
                    flush()
                else:
                    current = [position]
                pattern_index = (pattern_index + 1) % len(pattern)
                remaining = pattern[pattern_index]
                on = not on
        if on and current and current[-1] != position:
            current.append(position)
    flush()
    return pieces


def _stroke_polyline(
    builder: _MeshBuilder,
    points: list[Point],
    *,
    closed: bool,
    half: float,
    width: float,
    join: str,
    cap: str,
    miter_limit: float,
    tolerance: float,
) -> None:
    if len(points) < 2:
        if points:
            _emit_degenerate_cap(builder, points[0], half, cap)
        return

    directions = _segment_directions(points, closed)
    segment_count = len(points) if closed and len(directions) == len(points) else len(points) - 1
    valid_segments: list[tuple[Point, Point, Point]] = []  # (start, end, direction)
    for index in range(segment_count):
        direction = directions[index]
        if direction == (0.0, 0.0):
            continue
        start = points[index]
        end = points[(index + 1) % len(points)]
        valid_segments.append((start, end, direction))
        normal = (-direction[1] * half, direction[0] * half)
        builder.add_quad(
            (start[0] + normal[0], start[1] + normal[1]),
            (start[0] - normal[0], start[1] - normal[1]),
            (end[0] + normal[0], end[1] + normal[1]),
            (end[0] - normal[0], end[1] - normal[1]),
        )

    if not valid_segments:
        _emit_degenerate_cap(builder, points[0], half, cap)
        return

    for index in range(len(valid_segments) - 1):
        _emit_join(
            builder,
            vertex=valid_segments[index][1],
            dir_in=valid_segments[index][2],
            dir_out=valid_segments[index + 1][2],
            half=half,
            width=width,
            join=join,
            miter_limit=miter_limit,
        )
    if closed and len(valid_segments) >= 2:
        _emit_join(
            builder,
            vertex=valid_segments[-1][1],
            dir_in=valid_segments[-1][2],
            dir_out=valid_segments[0][2],
            half=half,
            width=width,
            join=join,
            miter_limit=miter_limit,
        )
    if not closed:
        first_start, _first_end, first_dir = valid_segments[0]
        _last_start, last_end, last_dir = valid_segments[-1]
        _emit_cap(builder, first_start, (-first_dir[0], -first_dir[1]), half, cap)
        _emit_cap(builder, last_end, last_dir, half, cap)


def _segment_directions(points: list[Point], closed: bool) -> list[Point]:
    directions: list[Point] = []
    for start, end in itertools.pairwise(points):
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length <= _MIN_SEGMENT_LENGTH:
            directions.append((0.0, 0.0))
        else:
            directions.append(((end[0] - start[0]) / length, (end[1] - start[1]) / length))
    if closed:
        seam_length = math.hypot(points[0][0] - points[-1][0], points[0][1] - points[-1][1])
        if seam_length > _MIN_SEGMENT_LENGTH:
            directions.append(
                (
                    (points[0][0] - points[-1][0]) / seam_length,
                    (points[0][1] - points[-1][1]) / seam_length,
                )
            )
    return directions


def _emit_join(
    builder: _MeshBuilder,
    *,
    vertex: Point,
    dir_in: Point,
    dir_out: Point,
    half: float,
    width: float,
    join: str,
    miter_limit: float,
) -> None:
    cross = dir_in[0] * dir_out[1] - dir_in[1] * dir_out[0]
    dot = dir_in[0] * dir_out[0] + dir_in[1] * dir_out[1]
    if abs(cross) < 1e-9 and dot >= 0.0:
        return  # collinear continuation

    # Outer side of the turn: left normal is (-dy, dx); a positive cross
    # (turn toward the left normal) leaves the gap on the right side.
    side = -1.0 if cross > 0.0 else 1.0
    n_in = (-dir_in[1] * half * side, dir_in[0] * half * side)
    n_out = (-dir_out[1] * half * side, dir_out[0] * half * side)
    outer_in = (vertex[0] + n_in[0], vertex[1] + n_in[1])
    outer_out = (vertex[0] + n_out[0], vertex[1] + n_out[1])

    if join == "round":
        start_angle = math.atan2(n_in[1], n_in[0])
        end_angle = math.atan2(n_out[1], n_out[0])
        sweep = _normalized_sweep(start_angle, end_angle)
        builder.add_fan(vertex, half, start_angle, sweep)
        return

    if join == "miter":
        m_x = n_in[0] + n_out[0]
        m_y = n_in[1] + n_out[1]
        m_len = math.hypot(m_x, m_y)
        if m_len > 1e-9:
            m_unit = (m_x / m_len, m_y / m_len)
            cos_half = (m_unit[0] * n_in[0] + m_unit[1] * n_in[1]) / half
            if cos_half > 1e-6:
                miter_len = half / cos_half
                # Qt: tip distance from the join point, in pen-width units.
                if miter_len <= miter_limit * width:
                    tip = (vertex[0] + m_unit[0] * miter_len, vertex[1] + m_unit[1] * miter_len)
                    builder.add_triangle(vertex, outer_in, tip)
                    builder.add_triangle(vertex, tip, outer_out)
                    return
        # fall through to bevel when over the limit or degenerate

    builder.add_triangle(vertex, outer_in, outer_out)


def _normalized_sweep(start_angle: float, end_angle: float) -> float:
    sweep = end_angle - start_angle
    while sweep <= -math.pi:
        sweep += 2.0 * math.pi
    while sweep > math.pi:
        sweep -= 2.0 * math.pi
    return sweep


def _emit_cap(
    builder: _MeshBuilder,
    point: Point,
    outward: Point,
    half: float,
    cap: str,
) -> None:
    if cap == "butt":
        return
    normal = (-outward[1] * half, outward[0] * half)
    if cap == "square":
        extend = (outward[0] * half, outward[1] * half)
        builder.add_quad(
            (point[0] + normal[0], point[1] + normal[1]),
            (point[0] - normal[0], point[1] - normal[1]),
            (point[0] + extend[0] + normal[0], point[1] + extend[1] + normal[1]),
            (point[0] + extend[0] - normal[0], point[1] + extend[1] - normal[1]),
        )
        return
    # round (Qt falls back to round for unknown cap tokens); sweep passes
    # through the outward direction so the semicircle covers the cap side.
    start_angle = math.atan2(normal[1], normal[0])
    builder.add_fan(point, half, start_angle, -math.pi)


def _emit_degenerate_cap(builder: _MeshBuilder, point: Point, half: float, cap: str) -> None:
    """Zero-length subpath: Qt draws a dot for round caps, a square for
    square caps, and nothing for butt caps."""

    if cap == "square":
        builder.add_quad(
            (point[0] - half, point[1] - half),
            (point[0] + half, point[1] - half),
            (point[0] - half, point[1] + half),
            (point[0] + half, point[1] + half),
        )
    elif cap != "butt":
        builder.add_fan(point, half, 0.0, math.pi)
        builder.add_fan(point, half, math.pi, math.pi)
