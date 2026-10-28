"""Visible, bounded grid primitives sharing the snap lattice definitions."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .layout_grid_model import LayoutGrid, Point, grid_basis, lattice_coordinates, lattice_point

MAX_GRID_PRIMITIVES = 4096
Bounds = tuple[float, float, float, float]
_HEX_OFFSETS = tuple((math.cos(k * math.pi / 3), math.sin(k * math.pi / 3)) for k in range(6))


@dataclass(frozen=True, slots=True)
class GridLine:
    start: Point
    end: Point
    major: bool = False


@dataclass(frozen=True, slots=True)
class GridGeometry:
    lines: tuple[GridLine, ...] = ()
    circles: tuple[tuple[float, float, float], ...] = ()


def clip_segment(start: Point, end: Point, bounds: Bounds) -> tuple[Point, Point] | None:
    x, y = start
    dx, dy = end[0] - x, end[1] - y
    left, top, right, bottom = bounds
    first, last = 0.0, 1.0
    for p, q in ((-dx, x - left), (dx, right - x), (-dy, y - top), (dy, bottom - y)):
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        parameter = q / p
        if p < 0:
            first = max(first, parameter)
        else:
            last = min(last, parameter)
        if first > last:
            return None
    return ((x + first * dx, y + first * dy), (x + last * dx, y + last * dy))


def _corners(bounds: Bounds) -> tuple[Point, ...]:
    left, top, right, bottom = bounds
    return (left, top), (right, top), (left, bottom), (right, bottom)


def _indices(low: float, high: float, budget: int) -> range:
    first, last = math.floor(low), math.ceil(high)
    stride = max(1, math.ceil((last - first + 1) / max(1, budget)))
    return range(first, last + 1, stride)


def _line(
    grid: LayoutGrid, start: Point, end: Point, bounds: Bounds, *, major: bool = False
) -> GridLine | None:
    clipped = clip_segment(start, end, bounds)
    return (
        None
        if clipped is None
        else GridLine(grid.canvas(clipped[0]), grid.canvas(clipped[1]), major)
    )


def _lattice_lines(grid: LayoutGrid, bounds: Bounds) -> list[GridLine]:
    a, b = grid_basis(grid)
    uv = [lattice_coordinates(a, b, point) for point in _corners(bounds)]
    lo_u, hi_u = min(p[0] for p in uv) - 1, max(p[0] for p in uv) + 1
    lo_v, hi_v = min(p[1] for p in uv) - 1, max(p[1] for p in uv) + 1
    budget = MAX_GRID_PRIMITIVES // (3 if grid.kind == "triangular" else 2)
    lines = []
    for family, low, high in ((0, lo_u, hi_u), (1, lo_v, hi_v)):
        for index in _indices(low, high, budget):
            start = (
                lattice_point(a, b, index, lo_v)
                if family == 0
                else lattice_point(a, b, lo_u, index)
            )
            end = (
                lattice_point(a, b, index, hi_v)
                if family == 0
                else lattice_point(a, b, hi_u, index)
            )
            line = _line(
                grid,
                start,
                end,
                bounds,
                major=grid.kind == "major" and index % grid.major_every == 0,
            )
            if line is not None:
                lines.append(line)
    if grid.kind == "triangular":
        for index in _indices(lo_u + lo_v, hi_u + hi_v, budget):
            line = _line(
                grid,
                lattice_point(a, b, lo_u, index - lo_u),
                lattice_point(a, b, hi_u, index - hi_u),
                bounds,
            )
            if line is not None:
                lines.append(line)
    return lines


def _hex_lines(grid: LayoutGrid, bounds: Bounds) -> list[GridLine]:
    a, b = grid_basis(grid)
    uv = [lattice_coordinates(a, b, point) for point in _corners(bounds)]
    iu = (math.floor(min(p[0] for p in uv)) - 2, math.ceil(max(p[0] for p in uv)) + 2)
    iv = (math.floor(min(p[1] for p in uv)) - 2, math.ceil(max(p[1] for p in uv)) + 2)
    count = (iu[1] - iu[0] + 1) * (iv[1] - iv[0] + 1)
    stride = max(1, math.ceil(math.sqrt(count * 3 / MAX_GRID_PRIMITIVES)))
    lines = []
    for i in range(iu[0], iu[1] + 1, stride):
        for j in range(iv[0], iv[1] + 1, stride):
            cx, cy = lattice_point(a, b, i, j)
            vertices = [(cx + grid.spacing * x, cy + grid.spacing * y) for x, y in _HEX_OFFSETS]
            for k in range(3):
                line = _line(grid, vertices[k], vertices[k + 1], bounds)
                if line is not None:
                    lines.append(line)
    return lines[:MAX_GRID_PRIMITIVES]


def _polar_geometry(grid: LayoutGrid, bounds: Bounds) -> GridGeometry:
    nearest = (max(bounds[0], min(0.0, bounds[2])), max(bounds[1], min(0.0, bounds[3])))
    min_radius = math.hypot(*nearest)
    max_radius = max(math.hypot(*point) for point in _corners(bounds))
    low = max(1, math.ceil(min_radius / grid.spacing))
    high = math.floor(max_radius / grid.spacing)
    budget = MAX_GRID_PRIMITIVES - grid.polar_divisions
    stride = max(1, math.ceil((high - low + 1) / budget))
    center = grid.canvas((0, 0))
    circles = tuple((*center, k * grid.spacing) for k in range(low, high + 1, stride))
    lines = []
    for ray in range(grid.polar_divisions):
        angle = ray * 2 * math.pi / grid.polar_divisions
        line = _line(
            grid, (0, 0), (max_radius * math.cos(angle), max_radius * math.sin(angle)), bounds
        )
        if line is not None:
            lines.append(line)
    return GridGeometry(tuple(lines), circles)


def visible_grid_geometry(grid: LayoutGrid, bounds: Bounds) -> GridGeometry:
    if (
        any(not math.isfinite(v) for v in bounds)
        or bounds[2] <= bounds[0]
        or bounds[3] <= bounds[1]
    ):
        return GridGeometry()
    points = [grid.local(point) for point in _corners(bounds)]
    local_bounds = (
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    )
    if grid.kind == "baseline":
        low = (local_bounds[1] - grid.baseline_start) / grid.baseline_spacing
        high = (local_bounds[3] - grid.baseline_start) / grid.baseline_spacing
        lines = []
        for index in _indices(low, high, MAX_GRID_PRIMITIVES):
            y = grid.baseline_start + index * grid.baseline_spacing
            line = _line(grid, (local_bounds[0], y), (local_bounds[2], y), local_bounds)
            if line is not None:
                lines.append(line)
        geometry = GridGeometry(tuple(lines))
    elif grid.kind == "polar":
        geometry = _polar_geometry(grid, local_bounds)
    else:
        lines = (
            _hex_lines(grid, local_bounds)
            if grid.kind == "hexagonal"
            else _lattice_lines(grid, local_bounds)
        )
        geometry = GridGeometry(tuple(lines[:MAX_GRID_PRIMITIVES]))
    clipped_lines = []
    for line in geometry.lines:
        clipped = clip_segment(line.start, line.end, bounds)
        if clipped is not None:
            clipped_lines.append(GridLine(*clipped, major=line.major))
    return GridGeometry(tuple(clipped_lines), geometry.circles)
