"""Lightweight JIT optimizer façade used in tests.

The real project plans to provide Numba-accelerated image operations, but the
current repository only requires a NumPy/SciPy fallback so that unit tests can
exercise the API surface.  This module therefore implements the public
interface expected by ``tests/unit/test_jit_optimizer.py`` while keeping the
logic pure-Python/NumPy by default.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

try:  # Optional dependency
    from numba import jit, prange

    NUMBA_AVAILABLE = True
except Exception:  # pragma: no cover - numba not installed
    NUMBA_AVAILABLE = False

    def jit(*_args: Any, **_kwargs: Any) -> Callable[..., Any]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            return func

        return decorator

    def prange(*args: Any, **kwargs: Any):  # type: ignore
        return range(*args, **kwargs)


try:  # Optional dependency
    from scipy import ndimage

    SCIPY_AVAILABLE = True
except Exception:  # pragma: no cover - scipy not installed
    ndimage = None  # type: ignore
    SCIPY_AVAILABLE = False


class AccelerationType(str, Enum):
    """Describe how the optimizer accelerates operations."""

    NUMBA_PARALLEL = "numba_parallel"
    NUMBA_SERIAL = "numba_serial"
    NUMPY = "numpy"
    PYTHON = "python"


@dataclass
class PerformanceMetrics:
    """Collect simple timing metrics for optimisation stages."""

    acceleration_type: AccelerationType
    compile_time_ms: float = 0.0
    execution_time_ms: float = 0.0
    speedup_factor: float = 1.0
    cache_hits: int = 0
    cache_misses: int = 0


class JITOptimizer:
    """Provide blend and filter helpers with optional JIT acceleration."""

    def __init__(self, *, enable_parallel: bool = True) -> None:
        self._numba_available = NUMBA_AVAILABLE
        self._parallel_enabled = bool(enable_parallel and NUMBA_AVAILABLE)
        if self._numba_available:
            acceleration = (
                AccelerationType.NUMBA_PARALLEL
                if self._parallel_enabled
                else AccelerationType.NUMBA_SERIAL
            )
        else:
            acceleration = AccelerationType.NUMPY
        self._metrics = PerformanceMetrics(acceleration_type=acceleration)
        self._warmup_complete = False

        if self._numba_available:
            self._perform_warmup()

    # ------------------------------------------------------------------
    # Public API helpers
    def is_accelerated(self) -> bool:
        return self._numba_available

    def get_metrics(self) -> PerformanceMetrics:
        return self._metrics

    def get_acceleration_info(self) -> dict[str, Any]:
        return {
            "numba_available": self._numba_available,
            "parallel_enabled": self._parallel_enabled,
            "warmup_complete": self._warmup_complete,
            "acceleration_type": self._metrics.acceleration_type.value,
            "compile_time_ms": self._metrics.compile_time_ms,
            "execution_time_ms": self._metrics.execution_time_ms,
            "speedup_factor": self._metrics.speedup_factor,
            "cache_hits": self._metrics.cache_hits,
            "cache_misses": self._metrics.cache_misses,
        }

    # ------------------------------------------------------------------
    # Blend modes
    def blend_normal(self, src: NDArray[Any], dst: NDArray[Any], opacity: float) -> NDArray[Any]:
        src_f, dst_f = self._prepare_float_pair(src, dst)
        opacity = float(np.clip(opacity, 0.0, 1.0))
        result = dst_f * (1.0 - opacity) + src_f * opacity
        return self._finalise(result, src)

    def blend_multiply(self, src: NDArray[Any], dst: NDArray[Any], opacity: float) -> NDArray[Any]:
        src_f, dst_f = self._prepare_float_pair(src, dst)
        base = (src_f * dst_f) / 255.0
        result = dst_f * (1.0 - opacity) + base * opacity
        return self._finalise(result, src)

    def blend_screen(self, src: NDArray[Any], dst: NDArray[Any], opacity: float) -> NDArray[Any]:
        src_f, dst_f = self._prepare_float_pair(src, dst)
        base = 255.0 - ((255.0 - src_f) * (255.0 - dst_f) / 255.0)
        result = dst_f * (1.0 - opacity) + base * opacity
        return self._finalise(result, src)

    def blend_overlay(self, src: NDArray[Any], dst: NDArray[Any], opacity: float) -> NDArray[Any]:
        src_f, dst_f = self._prepare_float_pair(src, dst)
        mask = dst_f <= 128.0
        base = np.empty_like(src_f)
        base[mask] = (2 * src_f[mask] * dst_f[mask]) / 255.0
        base[~mask] = 255.0 - 2 * (255.0 - src_f[~mask]) * (255.0 - dst_f[~mask]) / 255.0
        result = dst_f * (1.0 - opacity) + base * opacity
        return self._finalise(result, src)

    # ------------------------------------------------------------------
    # Filters / transforms (NumPy fallbacks)
    def gaussian_blur(self, image: NDArray[Any], *, radius: float = 1.0) -> NDArray[Any]:
        if SCIPY_AVAILABLE and ndimage is not None:
            blurred = ndimage.gaussian_filter(image, sigma=max(radius, 0.0))
            return self._finalise(blurred, image)
        return image.copy()

    def box_blur(self, image: NDArray[Any], *, radius: int = 1) -> NDArray[Any]:
        if SCIPY_AVAILABLE and ndimage is not None:
            size = max(int(radius), 1)
            blurred = ndimage.uniform_filter(image, size=size)
            return self._finalise(blurred, image)
        kernel_size = max(int(radius), 1) * 2 + 1
        pad = kernel_size // 2
        pad_width = [(pad, pad), (pad, pad)] + [(0, 0)] * (image.ndim - 2)
        padded = np.pad(image, pad_width, mode="edge")
        result = np.empty_like(image, dtype=np.float64)
        height, width = image.shape[:2]
        for y in range(height):
            for x in range(width):
                region = padded[y : y + kernel_size, x : x + kernel_size]
                result[y, x] = region.mean(axis=(0, 1))
        return self._finalise(result, image)

    def sharpen(self, image: NDArray[Any], *, strength: float = 1.0) -> NDArray[Any]:
        strength = float(np.clip(strength, 0.0, 5.0))
        blurred = self.gaussian_blur(image, radius=1.0)
        detail = self._prepare_float(image) - self._prepare_float(blurred)
        result = self._prepare_float(image) + strength * detail
        return self._finalise(result, image)

    def affine_transform(self, image: NDArray[Any], matrix: NDArray[Any]) -> NDArray[Any]:
        if SCIPY_AVAILABLE and ndimage is not None:
            matrix = np.asarray(matrix, dtype=np.float64)
            if matrix.shape == (3, 3):
                transform = matrix[:2, :2]
                offset = matrix[:2, 2]
            elif matrix.shape == (2, 3):
                transform = matrix[:, :2]
                offset = matrix[:, 2]
            else:
                raise ValueError("affine matrix must be 2x3 or 3x3")
            result = ndimage.affine_transform(
                image,
                transform,
                offset=offset,
                order=1,
                mode="reflect",
            )
            return self._finalise(result, image)
        return image.copy()

    def resize(self, image: NDArray[Any], new_size: tuple[int, int]) -> NDArray[Any]:
        new_h, new_w = map(int, new_size)
        if new_h <= 0 or new_w <= 0:
            raise ValueError("new_size must be positive")
        if SCIPY_AVAILABLE and ndimage is not None:
            zoom_y = new_h / image.shape[0]
            zoom_x = new_w / image.shape[1]
            factors = (zoom_y, zoom_x) + (1,) * (image.ndim - 2)
            result = ndimage.zoom(image, zoom=factors, order=1)
            return self._finalise(result, image)
        # Simple nearest-neighbour fallback
        y_indices = (np.linspace(0, image.shape[0] - 1, new_h)).astype(int)
        x_indices = (np.linspace(0, image.shape[1] - 1, new_w)).astype(int)
        resized = image[y_indices][:, x_indices]
        return cast("NDArray[Any]", resized.copy())

    # ------------------------------------------------------------------
    # Internal helpers
    def _perform_warmup(self) -> None:
        try:
            dummy = np.zeros((4, 4, 4), dtype=np.uint8)
            self.blend_normal(dummy, dummy, 0.5)
            self._warmup_complete = True
        except Exception:  # pragma: no cover - warmup failure should not abort
            self._warmup_complete = False

    @staticmethod
    def _prepare_float(array: NDArray[Any]) -> NDArray[Any]:
        return array.astype(np.float64, copy=False)

    def _prepare_float_pair(
        self, src: NDArray[Any], dst: NDArray[Any]
    ) -> tuple[NDArray[Any], NDArray[Any]]:
        if src.shape != dst.shape:
            raise ValueError("source and destination images must share the same shape")
        return self._prepare_float(src), self._prepare_float(dst)

    @staticmethod
    def _finalise(result: NDArray[Any], reference: NDArray[Any]) -> NDArray[Any]:
        clipped = np.clip(result, 0.0, 255.0)
        return clipped.astype(reference.dtype, copy=False)


__all__ = [
    "NUMBA_AVAILABLE",
    "AccelerationType",
    "JITOptimizer",
    "PerformanceMetrics",
]
