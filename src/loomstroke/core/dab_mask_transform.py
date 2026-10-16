"""Rotate one detached tip mask while retaining all of its coverage."""

import numpy as np
from PIL import Image


class DabMaskTransform:
    @staticmethod
    def rotate(mask: np.ndarray, angle: float) -> np.ndarray:
        if abs(angle) < 0.01:
            return mask
        image = Image.fromarray(np.clip(mask * 255, 0, 255).astype(np.uint8))
        rotated = image.rotate(angle, resample=Image.Resampling.BILINEAR, expand=True)
        return np.asarray(rotated, dtype=np.float32) / 255.0
