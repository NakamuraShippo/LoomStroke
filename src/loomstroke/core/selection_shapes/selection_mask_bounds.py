"""Locate nonzero coverage without allocating per-pixel coordinate pairs."""

import numpy as np


class SelectionMaskBounds:
    @staticmethod
    def find(mask):
        if mask is None:
            return None
        rows = np.flatnonzero(np.any(mask, axis=1))
        if not rows.size:
            return None
        columns = np.flatnonzero(np.any(mask, axis=0))
        x, y = int(columns[0]), int(rows[0])
        return x, y, int(columns[-1]) - x + 1, int(rows[-1]) - y + 1
