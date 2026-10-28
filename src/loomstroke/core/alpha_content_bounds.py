"""Pure, allocation-bounded alpha projection to a pixel bounding rectangle."""

import numpy as np


def alpha_content_bounds(alpha: np.ndarray) -> tuple[int, int, int, int] | None:
    if alpha.ndim != 2:
        raise ValueError("Alpha must be a two-dimensional plane")
    xs = np.flatnonzero(np.any(alpha, axis=0))
    if not xs.size:
        return None
    ys = np.flatnonzero(np.any(alpha, axis=1))
    return int(xs[0]), int(ys[0]), int(xs[-1] - xs[0] + 1), int(ys[-1] - ys[0] + 1)
