"""Canvas-anchored paper grain; no global random state or image I/O."""

from __future__ import annotations

import numpy as np


class PaperTextureMask:
    @staticmethod
    def sample(
        x: int, y: int, width: int, height: int, *, scale: float, strength: float, seed: int
    ) -> np.ndarray:
        # Each axis is hashed on its own row or column before the two are combined: the
        # per-pixel coordinate grids cost several times more than the grain itself.
        step = max(1.0, scale)
        xx = np.floor(np.arange(x, x + width) / step).astype(np.int64).astype(np.uint32)
        yy = np.floor(np.arange(y, y + height) / step).astype(np.int64).astype(np.uint32)
        hashed = (xx * np.uint32(73856093))[None, :] ^ (
            (yy * np.uint32(19349663)) ^ np.uint32(seed)
        )[:, None]
        hashed ^= hashed >> np.uint32(13)
        hashed *= np.uint32(1274126177)
        grain = (hashed & np.uint32(65535)).astype(np.float32) / 65535.0
        return 1.0 - min(1.0, max(0.0, strength)) * grain
