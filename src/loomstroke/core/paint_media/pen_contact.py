"""Contact of a tilted, pressure-sensitive pen on the brush-local pixel grid."""

import math

import numpy as np
from PIL import Image


class PenContact:
    def __init__(self, x, y, pad):
        self.x, self.y, self.pad = x, y, pad
        self.last = None
        self.mask = None
        # Optional tip coverage on the brush grid (soft edge, imported tip image).
        self.shape = None
        self.rotates = False

    def set_shape(self, shape, *, rotates=False):
        self.shape = np.ascontiguousarray(shape, dtype=np.float32)
        self.rotates = rotates
        self.last = None

    def _shape_at(self, angle):
        if not self.rotates or abs(angle) < 0.01:
            return self.shape
        image = Image.fromarray(np.clip(self.shape * 255, 0, 255).astype(np.uint8))
        rotated = image.rotate(angle, resample=Image.Resampling.BILINEAR)
        return np.asarray(rotated, dtype=np.float32) / 255.0

    def update(self, pressure, tilt_x, tilt_y, density=None, angle=0.0):
        """Set the dab's contact mask.

        density is the paint strength of this dab; None derives it from the pressure.
        """
        upright = pressure == 1.0 and tilt_x == tilt_y == 0
        if upright and density in (None, 1.0) and self.shape is None:
            self.last = self.mask = None
            return
        key = (pressure, tilt_x, tilt_y, density, angle if self.rotates else 0.0)
        if self.last == key:
            return
        weight = np.float32(pressure**0.65 if density is None else density)
        if upright:
            mask = np.full(self.x.shape, weight, dtype=np.float32)
        else:
            # Qt reports the tilt in degrees in each axis plane. The projected shaft
            # defines the long axis of an elliptical contact patch on the paper.
            tx = math.tan(math.radians(max(-85.0, min(85.0, tilt_x))))
            ty = math.tan(math.radians(max(-85.0, min(85.0, tilt_y))))
            slope = math.hypot(tx, ty)
            ux, uy = (tx / slope, ty / slope) if slope else (1.0, 0.0)
            sine = slope / math.hypot(1.0, slope)
            aspect = 1.0 - 0.54 * sine
            radius = self.pad * (0.42 + 0.58 * math.sqrt(pressure))
            along = self.x * ux + self.y * uy
            across = (self.y * ux - self.x * uy) / aspect
            distance = np.hypot(along, across)
            mask = np.clip(radius + 0.65 - distance, 0, 1) * weight
        if self.shape is not None:
            mask = mask * self._shape_at(angle)
        self.mask = mask
        self.last = key

    def apply(self, footprint):
        return footprint if self.mask is None else footprint * self.mask
