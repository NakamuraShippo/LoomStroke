"""Immutable grid parameters and canvas/local coordinate transforms."""

from __future__ import annotations

import math
from dataclasses import dataclass

GRID_KINDS = (
    "square",
    "major",
    "rectangular",
    "isometric",
    "triangular",
    "hexagonal",
    "polar",
    "baseline",
)
Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class LayoutGrid:
    kind: str = "square"
    spacing: float = 32.0
    y_spacing: float = 32.0
    major_every: int = 5
    origin: Point = (0.0, 0.0)
    rotation: float = 0.0
    polar_divisions: int = 12
    baseline_spacing: float = 20.0
    baseline_start: float = 0.0

    def __post_init__(self) -> None:
        if self.kind not in GRID_KINDS:
            raise ValueError("Invalid grid kind")
        if not all(
            math.isfinite(v) for v in (self.spacing, self.y_spacing, *self.origin, self.rotation)
        ):
            raise ValueError("Non-finite grid parameters")
        if min(self.spacing, self.y_spacing) < 1 or not 1 <= self.major_every <= 1000:
            raise ValueError("Invalid grid spacing")
        if not 3 <= self.polar_divisions <= 360:
            raise ValueError("Invalid polar divisions")
        if (
            not math.isfinite(self.baseline_spacing)
            or self.baseline_spacing <= 0
            or not math.isfinite(self.baseline_start)
        ):
            raise ValueError("Invalid baseline grid")

    def local(self, point: Point) -> Point:
        angle = math.radians(self.rotation)
        c, s = math.cos(angle), math.sin(angle)
        x, y = point[0] - self.origin[0], point[1] - self.origin[1]
        return x * c + y * s, -x * s + y * c

    def canvas(self, point: Point) -> Point:
        angle = math.radians(self.rotation)
        c, s = math.cos(angle), math.sin(angle)
        return (
            self.origin[0] + point[0] * c - point[1] * s,
            self.origin[1] + point[0] * s + point[1] * c,
        )


def grid_basis(grid: LayoutGrid) -> tuple[Point, Point]:
    step = grid.spacing
    if grid.kind == "isometric":
        return (step * math.sqrt(3) / 2, step / 2), (-step * math.sqrt(3) / 2, step / 2)
    if grid.kind in ("triangular", "hexagonal"):
        if grid.kind == "hexagonal":
            return (1.5 * step, math.sqrt(3) * step / 2), (0.0, math.sqrt(3) * step)
        return (step, 0.0), (step / 2, math.sqrt(3) * step / 2)
    return (step, 0.0), (0.0, grid.y_spacing if grid.kind == "rectangular" else step)


def lattice_point(a: Point, b: Point, i: float, j: float) -> Point:
    return a[0] * i + b[0] * j, a[1] * i + b[1] * j


def lattice_coordinates(a: Point, b: Point, point: Point) -> Point:
    determinant = a[0] * b[1] - a[1] * b[0]
    return (point[0] * b[1] - point[1] * b[0]) / determinant, (
        a[0] * point[1] - a[1] * point[0]
    ) / determinant
