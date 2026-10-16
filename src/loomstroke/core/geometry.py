"""Geometry helpers for affine transforms."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(slots=True)
class Affine2D:
    """2D affine transform mapping payload-local -> canvas.

    x' = a*x + b*y + tx
    y' = c*x + d*y + ty
    """

    a: float
    b: float
    c: float
    d: float
    tx: float
    ty: float

    @staticmethod
    def identity() -> Affine2D:
        return Affine2D(1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    @staticmethod
    def translation(tx: float, ty: float) -> Affine2D:
        return Affine2D(1.0, 0.0, 0.0, 1.0, float(tx), float(ty))

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return (self.a * x + self.b * y + self.tx, self.c * x + self.d * y + self.ty)

    def inverse(self) -> Affine2D | None:
        det = self.a * self.d - self.b * self.c
        if abs(det) <= 1e-10 or not math.isfinite(det):
            return None
        inv_a = self.d / det
        inv_b = -self.b / det
        inv_c = -self.c / det
        inv_d = self.a / det
        inv_tx = -(inv_a * self.tx + inv_b * self.ty)
        inv_ty = -(inv_c * self.tx + inv_d * self.ty)
        return Affine2D(inv_a, inv_b, inv_c, inv_d, inv_tx, inv_ty)
