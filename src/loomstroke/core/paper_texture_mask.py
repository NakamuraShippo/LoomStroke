"""Canvas-anchored paper grain; no global random state or image I/O."""

from __future__ import annotations

import numpy as np


class PaperTextureMask:
    @staticmethod
    def sample(
        x: int, y: int, width: int, height: int, *, scale: float, strength: float, seed: int
    ) -> np.ndarray:
        yy, xx = np.mgrid[y : y + height, x : x + width]
        xx = np.floor(xx / max(1.0, scale)).astype(np.int64).astype(np.uint32)
        yy = np.floor(yy / max(1.0, scale)).astype(np.int64).astype(np.uint32)
        hashed = (xx * np.uint32(73856093)) ^ (yy * np.uint32(19349663)) ^ np.uint32(seed)
        hashed ^= hashed >> np.uint32(13)
        hashed *= np.uint32(1274126177)
        grain = (hashed & np.uint32(65535)).astype(np.float32) / 65535.0
        return 1.0 - min(1.0, max(0.0, strength)) * grain
