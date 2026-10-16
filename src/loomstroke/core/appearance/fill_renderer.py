"""Regenerates fill pixels from a ``FillSpec`` at any size (solid / gradient / pattern)."""

from __future__ import annotations

import math
from typing import Any, ClassVar

import numpy as np

from .appearance_registry import AppearanceRegistry
from .fill_spec import FillSpec

__all__ = ["FillRenderer"]


class FillRenderer:
    MAX_PIXELS: ClassVar[int] = 64 * 1024 * 1024

    def __init__(self, registry: AppearanceRegistry | None = None) -> None:
        self._registry = registry or AppearanceRegistry(include_adjustments=False)

    def render(self, spec: FillSpec, width: int, height: int) -> np.ndarray:
        """Straight-alpha RGBA ``(height, width, 4)`` uint8."""
        width, height = max(1, int(width)), max(1, int(height))
        if width * height > self.MAX_PIXELS:
            raise ValueError("fill_too_large")
        params, _issues = self._registry.validate("fill", spec.kind, spec.params)
        if not spec.enabled:
            return np.zeros((height, width, 4), dtype=np.uint8)
        if spec.kind == "solid":
            rgba = self._solid(params, width, height)
        elif spec.kind == "gradient":
            rgba = self._gradient(params, width, height)
        elif spec.kind == "pattern":
            rgba = self._pattern(params, width, height)
        else:
            return np.zeros((height, width, 4), dtype=np.uint8)
        opacity = float(params.get("opacity", 1.0))
        if opacity < 1.0:
            rgba[..., 3] = (rgba[..., 3].astype(np.float32) * opacity).astype(np.uint8)
        return rgba

    # -- kinds -------------------------------------------------------------------------

    @staticmethod
    def _solid(params: dict[str, Any], width: int, height: int) -> np.ndarray:
        color = np.array(params["color"], dtype=np.uint8)
        return np.broadcast_to(color, (height, width, 4)).copy()

    @staticmethod
    def _gradient(params: dict[str, Any], width: int, height: int) -> np.ndarray:
        start = np.array(params["color_start"], dtype=np.float32)
        end = np.array(params["color_end"], dtype=np.float32)
        ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
        if params.get("shape") == "radial":
            cx, cy = params["center"]
            dx = (xs + 0.5) / width - float(cx)
            dy = (ys + 0.5) / height - float(cy)
            t = np.clip(np.hypot(dx, dy) / 0.5, 0.0, 1.0)
        else:
            angle = math.radians(float(params.get("angle", 0.0)))
            ux, uy = math.cos(angle), math.sin(angle)
            proj = ((xs + 0.5) / width - 0.5) * ux + ((ys + 0.5) / height - 0.5) * uy
            extent = 0.5 * (abs(ux) + abs(uy)) or 1.0
            t = np.clip(proj / extent * 0.5 + 0.5, 0.0, 1.0)
        rgba = start[None, None, :] * (1.0 - t[..., None]) + end[None, None, :] * t[..., None]
        return np.clip(np.rint(rgba), 0, 255).astype(np.uint8)

    @staticmethod
    def _pattern(params: dict[str, Any], width: int, height: int) -> np.ndarray:
        size = max(1.0, float(params.get("size", 16.0)))
        angle = math.radians(float(params.get("angle", 0.0)))
        ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
        ux, uy = math.cos(angle), math.sin(angle)
        u = (xs + 0.5) * ux + (ys + 0.5) * uy
        v = -(xs + 0.5) * uy + (ys + 0.5) * ux
        kind = params.get("pattern", "checker")
        if kind == "stripes":
            mask = np.floor(u / size).astype(np.int64) % 2 == 0
        elif kind == "dots":
            fu = np.mod(u, size) - size / 2.0
            fv = np.mod(v, size) - size / 2.0
            mask = np.hypot(fu, fv) <= size / 4.0
        else:
            mask = (
                np.floor(u / size).astype(np.int64) + np.floor(v / size).astype(np.int64)
            ) % 2 == 0
        fg = np.array(params["foreground"], dtype=np.uint8)
        bg = np.array(params["background"], dtype=np.uint8)
        return np.where(mask[..., None], fg[None, None, :], bg[None, None, :]).astype(np.uint8)
