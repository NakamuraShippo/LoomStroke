"""Manga screentone live-filter filter (CT-02, ``filter_type="manga_screentone"``).

The filter turns its carrier (the raster layer it is parented to, or the
running composite for a sibling filter layer) into a halftone screen: the
tone colour is laid down where the carrier has alpha, masked by the screen
coverage from :class:`ScreentoneRenderer`.  Painting / erasing the carrier
therefore adds / removes tone without touching the tone definition.

CPU (this class) and GPU (``WgpuFilterProcessor``) share every number
through :func:`screentone_gpu_uniforms`, so the canvas, merge / flatten and
export agree pixel for pixel.  The document DPI and the carrier's canvas
position are not known to ``BaseFilter.apply``; callers inject them with
:func:`screentone_runtime_params` right before applying (transient keys,
never persisted).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from loomstroke.core.mask_coverage import mask_coverage_alpha8
from loomstroke.core.screentone.screentone_normalizer import ScreentoneNormalizer
from loomstroke.core.screentone.screentone_params import (
    ORIGIN_MODES,
    PATTERNS,
    PREVIEW_POLICIES,
    ScreentoneParams,
)
from loomstroke.core.screentone.screentone_renderer import ScreentoneRenderer
from loomstroke.core.screentone.screentone_validator import ScreentoneValidator
from loomstroke.core.screentone.spot_functions import SpotFunctionTable

from . import BaseFilter, FilterParamSchema
from .gaussian_blur import _bgra_to_qimage, _qimage_to_bgra

FILTER_TYPE = "manga_screentone"
EFFECT_TYPE = FILTER_TYPE  # Legacy import compatibility.
#: Transient keys injected at apply time (never written to ``filter_params``).
RUNTIME_DPI_KEY = "_document_dpi"
RUNTIME_ORIGIN_KEY = "_layer_origin"

#: GPU pattern ids (``p4``); the WGSL switch and the parity test mirror them.
GPU_PATTERN_IDS: dict[str, int] = {
    "round": 0,
    "ellipse": 1,
    "line": 2,
    "cross": 3,
    "stochastic": 4,
}
GPU_PATTERN_SOLID = 5
#: ``SpotFunctionTable.threshold`` returns ``inf`` for 100% density; the
#: shader gets a large finite number that clamps to full coverage.
_GPU_FULL_THRESHOLD = 1.0e6

_NORMALIZER = ScreentoneNormalizer()
_VALIDATOR = ScreentoneValidator()
_RENDERER = ScreentoneRenderer(_VALIDATOR)


def screentone_runtime_params(
    params: Mapping[str, Any] | None,
    *,
    document_dpi: float | None,
    layer_origin: tuple[int, int] | tuple[float, float] = (0, 0),
) -> dict[str, Any]:
    """Copy of *params* carrying the apply-time context the filter needs."""
    runtime = dict(params) if isinstance(params, Mapping) else {}
    if document_dpi is not None:
        runtime[RUNTIME_DPI_KEY] = float(document_dpi)
    runtime[RUNTIME_ORIGIN_KEY] = (float(layer_origin[0]), float(layer_origin[1]))
    return runtime


def screentone_document_dpi(params: Mapping[str, Any]) -> float | None:
    value = params.get(RUNTIME_DPI_KEY)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 1.0 else None


def screentone_layer_origin(params: Mapping[str, Any]) -> tuple[float, float]:
    value = params.get(RUNTIME_ORIGIN_KEY)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return (float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return (0.0, 0.0)
    return (0.0, 0.0)


def screentone_phase_origin(
    normalized: ScreentoneParams, layer_origin: tuple[float, float]
) -> tuple[float, float]:
    """Canvas point the screen grid is anchored at (page / local / custom)."""
    if normalized.origin_mode == "local":
        return (float(layer_origin[0]), float(layer_origin[1]))
    if normalized.origin_mode == "custom":
        return (float(normalized.origin_px[0]), float(normalized.origin_px[1]))
    return (0.0, 0.0)


def screentone_gpu_uniforms(params: Mapping[str, Any]) -> list[float]:
    """``p0..p14`` for the WGSL pass, derived from the same normalized params
    the CPU reference uses.  Layout:

    ``p0`` pitch px, ``p1`` cos(angle), ``p2`` sin(angle), ``p3`` spot
    threshold, ``p4`` pattern id, ``p5`` dot aspect, ``p6/p7`` phase origin
    (canvas px), ``p8`` tone RGB packed ``r*65536+g*256+b``, ``p9`` tone alpha
    0..1, ``p10`` density 0..1, ``p11`` seed (24 bit), ``p12/p13`` layer
    origin (canvas px), ``p14`` unused.
    """
    normalized = _NORMALIZER.normalize(params)
    dpi = screentone_document_dpi(params)
    origin = screentone_layer_origin(params)
    pitch = _VALIDATOR.pixel_pitch(normalized, dpi)
    theta = math.radians(float(normalized.angle_deg))
    if normalized.preview_policy == "solid_fallback":
        pattern_id = GPU_PATTERN_SOLID
    else:
        pattern_id = GPU_PATTERN_IDS.get(normalized.pattern, 0)
    threshold = SpotFunctionTable.threshold(
        normalized.pattern, normalized.density, dot_aspect=normalized.dot_aspect
    )
    if math.isinf(threshold):
        threshold = _GPU_FULL_THRESHOLD
    phase = screentone_phase_origin(normalized, origin)
    r, g, b, a = normalized.tone_rgba
    return [
        float(pitch),
        math.cos(theta),
        math.sin(theta),
        float(threshold),
        float(pattern_id),
        float(normalized.dot_aspect),
        float(phase[0]),
        float(phase[1]),
        float(r * 65536 + g * 256 + b),
        float(a) / 255.0,
        float(normalized.density),
        float(int(normalized.seed) & 0xFFFFFF),
        float(origin[0]),
        float(origin[1]),
        0.0,
    ]


class MangaScreentoneFilter(BaseFilter):
    """Halftone screen over the carrier's coverage (dot / line / cross / FM)."""

    def name(self) -> str:
        return "Manga Screentone"

    def category(self) -> str:
        return "manga"

    def param_schema(self) -> list[FilterParamSchema]:
        return [
            FilterParamSchema(
                key="pattern",
                label="Pattern",
                param_type="enum",
                default="round",
                choices=list(PATTERNS),
            ),
            FilterParamSchema(
                key="frequency_lpi",
                label="Frequency (LPI)",
                param_type="float",
                default=60.0,
                min_val=5.0,
                max_val=200.0,
                step=1.0,
            ),
            FilterParamSchema(
                key="density_percent",
                label="Density (%)",
                param_type="float",
                default=20.0,
                min_val=0.0,
                max_val=100.0,
                step=1.0,
            ),
            FilterParamSchema(
                key="angle_deg",
                label="Angle",
                param_type="float",
                default=45.0,
                min_val=-180.0,
                max_val=180.0,
                step=1.0,
            ),
            FilterParamSchema(
                key="dot_aspect",
                label="Dot Aspect",
                param_type="float",
                default=1.0,
                min_val=0.25,
                max_val=4.0,
                step=0.05,
            ),
            FilterParamSchema(
                key="origin_mode",
                label="Origin",
                param_type="enum",
                default="page",
                choices=list(ORIGIN_MODES),
            ),
            FilterParamSchema(
                key="seed",
                label="Seed",
                param_type="int",
                default=0,
                min_val=0,
                max_val=16777215,
                step=1,
            ),
            FilterParamSchema(
                key="preview_policy",
                label="Preview",
                param_type="enum",
                default="moire_safe",
                choices=list(PREVIEW_POLICIES),
            ),
        ]

    def apply(
        self,
        source: QImage,
        params: dict[str, Any],
        mask: QImage | None = None,
    ) -> QImage:
        normalized = _NORMALIZER.normalize(params)
        dpi = screentone_document_dpi(params)
        origin = screentone_layer_origin(params)
        bgra = _qimage_to_bgra(source)
        height, width = bgra.shape[:2]
        if width == 0 or height == 0:
            return source
        bounds = (round(origin[0]), round(origin[1]), width, height)
        phase = screentone_phase_origin(normalized, origin)
        coverage = _RENDERER.render_coverage(normalized, dpi, bounds, origin_px=phase)
        tone_r, tone_g, tone_b, tone_a = normalized.tone_rgba
        alpha = (
            bgra[:, :, 3].astype(np.float32)
            * (coverage.astype(np.float32) / 255.0)
            * (float(tone_a) / 255.0)
        )
        result = np.empty_like(bgra)
        result[:, :, 3] = np.round(alpha).astype(np.uint8)
        scale = alpha / 255.0
        result[:, :, 0] = np.round(tone_b * scale).astype(np.uint8)
        result[:, :, 1] = np.round(tone_g * scale).astype(np.uint8)
        result[:, :, 2] = np.round(tone_r * scale).astype(np.uint8)
        if mask is not None and not mask.isNull():
            result = self._apply_mask(bgra, result, mask)
        return _bgra_to_qimage(np.ascontiguousarray(result))

    @staticmethod
    def _apply_mask(original: np.ndarray, result: np.ndarray, mask: QImage) -> np.ndarray:
        """Mix effected over original by the canonical coverage of *mask*."""
        height, width = original.shape[:2]
        coverage = mask_coverage_alpha8(mask)
        if coverage.width() != width or coverage.height() != height:
            coverage = coverage.scaled(
                width,
                height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        bits = coverage.constBits()
        if hasattr(bits, "setsize"):
            bits.setsize(coverage.bytesPerLine() * height)
        rows = np.frombuffer(bits, dtype=np.uint8, count=coverage.bytesPerLine() * height)
        weight = rows.reshape(height, coverage.bytesPerLine())[:, :width].astype(np.float32) / 255.0
        weight = weight[:, :, None]
        mixed = original.astype(np.float32) * (1.0 - weight) + result.astype(np.float32) * weight
        return np.round(mixed).astype(np.uint8)


MangaScreentoneEffect = MangaScreentoneFilter  # Legacy class import compatibility.
BaseEffect = BaseFilter
EffectParamSchema = FilterParamSchema
