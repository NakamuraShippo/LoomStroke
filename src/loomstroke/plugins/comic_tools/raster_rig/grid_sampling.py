"""Interpolate pixel-aligned coarse fields without image-resize offsets."""

from __future__ import annotations

import numpy as np


def expand_grid(values: np.ndarray, width: int, height: int, step: int) -> np.ndarray:
    """Sample a field whose knots are at (column * step, row * step).

    Callers include an extra boundary knot past the destination extent.
    Unlike image resizing, this mapping has no half-pixel shift and its
    scale does not depend on the rounded-up grid dimensions.
    """
    import cv2  # noqa: PLC0415

    inverse = np.array([[1.0 / step, 0.0, 0.0], [0.0, 1.0 / step, 0.0]], dtype=np.float64)
    return cv2.warpAffine(
        np.asarray(values, dtype=np.float32),
        inverse,
        (width, height),
        flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
        borderMode=cv2.BORDER_REPLICATE,
    )
