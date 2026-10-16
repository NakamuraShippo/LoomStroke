"""Immutable CMYK plus independent alpha, safe for save workers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from .color_transform import CmykColorTransform


@dataclass(frozen=True, slots=True, eq=False)
class NativeCmykPixels:
    inks: np.ndarray
    alpha: np.ndarray
    profile: bytes

    def __post_init__(self) -> None:
        inks, alpha = np.asarray(self.inks), np.asarray(self.alpha)
        if inks.ndim != 3 or inks.shape[2] != 4 or inks.dtype not in (np.uint8, np.uint16):
            raise ValueError("CMYK requires H x W x 4 unsigned 8/16-bit samples")
        if alpha.shape != inks.shape[:2] or alpha.dtype != inks.dtype:
            raise ValueError("Alpha must have the same size and bit depth as the ink planes")
        if min(inks.shape[:2]) < 1 or inks.nbytes + alpha.nbytes > 256 * 1024 * 1024:
            raise ValueError("CMYK layer exceeds the decoded layer budget")
        profile = CmykColorTransform.validate_profile(bytes(self.profile))
        # Immutable bytes, rather than only an ndarray writeable flag which a caller can undo.
        object.__setattr__(
            self, "inks", np.frombuffer(inks.tobytes(), inks.dtype).reshape(inks.shape)
        )
        object.__setattr__(
            self, "alpha", np.frombuffer(alpha.tobytes(), alpha.dtype).reshape(alpha.shape)
        )
        object.__setattr__(self, "profile", profile)

    @property
    def bits(self) -> int:
        return self.inks.dtype.itemsize * 8

    @property
    def profile_hash(self) -> str:
        return hashlib.sha256(self.profile).hexdigest()

    def preview(self) -> np.ndarray:
        return CmykColorTransform.to_rgb(self.inks, self.alpha, self.profile)
