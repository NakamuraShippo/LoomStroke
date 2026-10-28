"""Nearest grid intersections in constant-size neighborhoods, independent of Qt."""

from __future__ import annotations

import math

from .layout_grid_model import LayoutGrid, Point, grid_basis, lattice_coordinates, lattice_point


def _nearest_lattice(grid: LayoutGrid, point: Point) -> Point:
    a, b = grid_basis(grid)
    u, v = lattice_coordinates(a, b, point)
    i, j = round(u), round(v)
    candidates = [lattice_point(a, b, i + di, j + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1)]
    return min(candidates, key=lambda p: math.dist(p, point))


def _nearest_hex_vertex(grid: LayoutGrid, point: Point) -> Point:
    a, b = grid_basis(grid)
    u, v = lattice_coordinates(a, b, point)
    i, j = round(u), round(v)
    candidates = []
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            cx, cy = lattice_point(a, b, i + di, j + dj)
            for k in range(6):
                angle = k * math.pi / 3
                candidates.append(
                    (cx + grid.spacing * math.cos(angle), cy + grid.spacing * math.sin(angle))
                )
    return min(candidates, key=lambda p: math.dist(p, point))


def _nearest_polar(grid: LayoutGrid, point: Point) -> Point:
    angle_step = 2 * math.pi / grid.polar_divisions
    ray = round(math.atan2(point[1], point[0]) / angle_step)
    candidates = [(0.0, 0.0)]
    for offset in (-1, 0, 1):
        angle = (ray + offset) * angle_step
        c, s = math.cos(angle), math.sin(angle)
        ring = max(0, round((point[0] * c + point[1] * s) / grid.spacing))
        candidates.append((ring * grid.spacing * c, ring * grid.spacing * s))
    return min(candidates, key=lambda p: math.dist(p, point))


def nearest_grid_point(grid: LayoutGrid, point: Point) -> Point:
    x, y = grid.local(point)
    if grid.kind == "baseline":
        result = (
            x,
            grid.baseline_start
            + round((y - grid.baseline_start) / grid.baseline_spacing) * grid.baseline_spacing,
        )
    elif grid.kind in ("square", "major", "rectangular"):
        sy = grid.y_spacing if grid.kind == "rectangular" else grid.spacing
        result = (round(x / grid.spacing) * grid.spacing, round(y / sy) * sy)
    elif grid.kind == "hexagonal":
        result = _nearest_hex_vertex(grid, (x, y))
    elif grid.kind == "polar":
        result = _nearest_polar(grid, (x, y))
    else:
        result = _nearest_lattice(grid, (x, y))
    return grid.canvas(result)
