"""Spot functions and density thresholds for AM screens (CT-02).

A spot function ``f(u, v)`` over the unit screen cell (``u, v`` in
``[-0.5, 0.5)``) orders the pixels of the cell; ink is everywhere ``f <
threshold``.  The threshold for a requested coverage is the empirical
quantile of ``f`` sampled over the cell, so every pattern reproduces the
requested density to within the sampling resolution regardless of its
shape (dots, ellipses, lines, crosses) — the CPU reference and the GPU pass
share the same thresholds.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from numpy.typing import NDArray

_SAMPLES = 256


class SpotFunctionTable:
    """Evaluate spot functions and map densities to thresholds."""

    @staticmethod
    def evaluate(
        pattern: str,
        fu: NDArray[np.floating],
        fv: NDArray[np.floating],
        *,
        dot_aspect: float = 1.0,
    ) -> NDArray[np.float32]:
        """Spot value per sample; ``fu``/``fv`` are cell-relative in ``[-0.5, 0.5)``."""
        if pattern == "line":
            value = np.abs(fv)
        elif pattern == "cross":
            value = np.minimum(np.abs(fu), np.abs(fv))
        elif pattern == "ellipse":
            aspect = max(float(dot_aspect), 1e-3)
            value = np.sqrt((fu / aspect) ** 2 + (fv * aspect) ** 2)
        else:  # round (and the fallback for unknown patterns)
            value = np.sqrt(fu * fu + fv * fv)
        return np.asarray(value, dtype=np.float32)

    @classmethod
    def threshold(cls, pattern: str, density: float, *, dot_aspect: float = 1.0) -> float:
        """Spot threshold whose sub-level set covers ``density`` of the cell."""
        clamped = max(0.0, min(1.0, float(density)))
        if clamped <= 0.0:
            return -1.0  # nothing inks
        if clamped >= 1.0:
            return float("inf")  # everything inks
        sorted_values = cls._sorted_samples(pattern, round(float(dot_aspect), 4))
        index = round(clamped * (sorted_values.size - 1))
        return float(sorted_values[index])

    @staticmethod
    @lru_cache(maxsize=64)
    def _sorted_samples(pattern: str, dot_aspect: float) -> NDArray[np.float32]:
        axis = (np.arange(_SAMPLES, dtype=np.float64) + 0.5) / _SAMPLES - 0.5
        fu, fv = np.meshgrid(axis, axis, indexing="xy")
        values = SpotFunctionTable.evaluate(pattern, fu, fv, dot_aspect=dot_aspect)
        return np.sort(values.ravel())
