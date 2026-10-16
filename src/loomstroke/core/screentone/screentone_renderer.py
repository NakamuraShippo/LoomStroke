"""CPU reference renderer for screentone coverage (CT-02 spec §段階導入 1).

Deterministic: the same params, DPI, bounds and scale always yield the
same bytes (export golden / GPU parity).  The renderer produces *coverage*
(0 = no ink, 255 = full ink) over a pixel rectangle in canvas space; the
effect layer multiplies it with the carrier's alpha and colours it with
``tone_rgba``.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from .screentone_params import MIN_PITCH_PX, ScreentoneParams
from .screentone_validator import ScreentoneValidator
from .spot_functions import SpotFunctionTable


class ScreentoneRenderer:
    """Render tone coverage for a rectangle of canvas pixels."""

    def __init__(self, validator: ScreentoneValidator | None = None) -> None:
        self._validator = validator or ScreentoneValidator()

    def render_coverage(
        self,
        params: ScreentoneParams,
        document_dpi: float | None,
        bounds: tuple[int, int, int, int],
        *,
        output_scale: float = 1.0,
        origin_px: tuple[float, float] | None = None,
    ) -> NDArray[np.uint8]:
        """Coverage for ``bounds = (x, y, width, height)`` in canvas pixels.

        ``output_scale`` is the display magnification (1.0 = 100%).  Under the
        ``moire_safe`` policy, a screen finer than 2 device pixels renders as
        its uniform density instead of an aliased pattern; ``accurate`` always
        renders the pattern; ``solid_fallback`` always renders the density.
        ``origin_px`` overrides the phase origin (``origin_mode == "local"``:
        the caller passes the layer origin; ``custom``: ``params.origin_px``).
        """
        x0, y0, width, height = (int(v) for v in bounds)
        if width <= 0 or height <= 0:
            return np.zeros((max(height, 0), max(width, 0)), dtype=np.uint8)
        density = params.density
        if self._use_solid(params, document_dpi, output_scale):
            return np.full((height, width), round(density * 255.0), dtype=np.uint8)
        if params.pattern == "stochastic":
            return self._stochastic(params, x0, y0, width, height, density)
        pitch = self._validator.pixel_pitch(params, document_dpi)
        origin = self._origin(params, origin_px)
        xs = np.arange(width, dtype=np.float64) + (x0 + 0.5 - origin[0])
        ys = np.arange(height, dtype=np.float64) + (y0 + 0.5 - origin[1])
        dx, dy = np.meshgrid(xs, ys, indexing="xy")
        theta = math.radians(float(params.angle_deg))
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        u = (dx * cos_t + dy * sin_t) / pitch
        v = (-dx * sin_t + dy * cos_t) / pitch
        fu = u - np.floor(u) - 0.5
        fv = v - np.floor(v) - 0.5
        spot = SpotFunctionTable.evaluate(params.pattern, fu, fv, dot_aspect=params.dot_aspect)
        threshold = SpotFunctionTable.threshold(
            params.pattern, density, dot_aspect=params.dot_aspect
        )
        if math.isinf(threshold):
            return np.full((height, width), 255, dtype=np.uint8)
        if threshold < 0.0:
            return np.zeros((height, width), dtype=np.uint8)
        # One device pixel of anti-aliasing: spot values change by ~1/pitch
        # per canvas pixel, so the edge band is 1/pitch wide in spot units.
        edge = 1.0 / max(pitch, 1e-6)
        coverage = np.clip((threshold - spot) / edge + 0.5, 0.0, 1.0)
        return np.asarray(np.round(coverage * 255.0), dtype=np.uint8)

    def render_rgba(
        self,
        params: ScreentoneParams,
        document_dpi: float | None,
        bounds: tuple[int, int, int, int],
        *,
        carrier_alpha: NDArray[np.uint8] | None = None,
        output_scale: float = 1.0,
        origin_px: tuple[float, float] | None = None,
    ) -> NDArray[np.uint8]:
        """Straight RGBA8 tone raster: ``tone_rgba`` coloured, alpha = coverage x carrier."""
        coverage = self.render_coverage(
            params, document_dpi, bounds, output_scale=output_scale, origin_px=origin_px
        )
        height, width = coverage.shape
        alpha = coverage.astype(np.float32) * (float(params.tone_rgba[3]) / 255.0)
        if carrier_alpha is not None:
            carrier = np.asarray(carrier_alpha, dtype=np.float32)
            if carrier.shape != coverage.shape:
                raise ValueError("carrier_alpha must match the tone bounds")
            alpha = alpha * (carrier / 255.0)
        rgba = np.empty((height, width, 4), dtype=np.uint8)
        rgba[..., 0] = int(params.tone_rgba[0])
        rgba[..., 1] = int(params.tone_rgba[1])
        rgba[..., 2] = int(params.tone_rgba[2])
        rgba[..., 3] = np.asarray(np.round(np.clip(alpha, 0.0, 255.0)), dtype=np.uint8)
        return rgba

    # -- helpers ----------------------------------------------------------------

    def _use_solid(
        self, params: ScreentoneParams, document_dpi: float | None, output_scale: float
    ) -> bool:
        if params.preview_policy == "solid_fallback":
            return True
        if params.preview_policy != "moire_safe" or params.pattern == "stochastic":
            # An FM screen has no pitch; it cannot alias the way an AM grid does.
            return False
        scale = float(output_scale)
        if not math.isfinite(scale) or scale <= 0.0:
            return False
        return self._validator.pixel_pitch(params, document_dpi) * scale < MIN_PITCH_PX

    @staticmethod
    def _origin(
        params: ScreentoneParams, origin_px: tuple[float, float] | None
    ) -> tuple[float, float]:
        if origin_px is not None:
            return (float(origin_px[0]), float(origin_px[1]))
        if params.origin_mode == "custom":
            return (float(params.origin_px[0]), float(params.origin_px[1]))
        return (0.0, 0.0)

    @staticmethod
    def _stochastic(
        params: ScreentoneParams,
        x0: int,
        y0: int,
        width: int,
        height: int,
        density: float,
    ) -> NDArray[np.uint8]:
        """Seeded per-pixel hash threshold (FM screen); independent of DPI.

        32-bit wrapping arithmetic on the canvas pixel coordinates and a
        24-bit seed, mirrored exactly by the WGSL pass so CPU and GPU agree.
        """
        xs = np.arange(x0, x0 + width, dtype=np.int64).astype(np.int32).view(np.uint32)
        ys = np.arange(y0, y0 + height, dtype=np.int64).astype(np.int32).view(np.uint32)
        gx, gy = np.meshgrid(xs, ys, indexing="xy")
        seed = np.uint32(int(params.seed) & 0xFFFFFF)
        with np.errstate(over="ignore"):
            h = (
                (gx * np.uint32(0x9E3779B1))
                ^ (gy * np.uint32(0x85EBCA77))
                ^ (seed * np.uint32(0xC2B2AE3D))
            )
            h ^= h >> np.uint32(15)
            h *= np.uint32(0x2C1B3C6D)
            h ^= h >> np.uint32(12)
            h *= np.uint32(0x297A2D39)
            h ^= h >> np.uint32(15)
        unit = (h & np.uint32(0xFFFFFF)).astype(np.float64) / float(0x1000000)
        return np.where(unit < density, 255, 0).astype(np.uint8)
