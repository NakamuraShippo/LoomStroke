"""Preview light helpers."""

from __future__ import annotations

import numpy as np


def normalized_light_direction(direction: tuple[float, float, float]) -> np.ndarray:
    vector = np.asarray(direction, dtype=np.float32)
    length = float(np.linalg.norm(vector))
    if length <= 1e-6:
        return np.asarray((0.3, 0.6, 1.0), dtype=np.float32)
    return vector / length
