"""What a paste receives after decoding (CT-19 spec 19 §8.2)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

__all__ = ["DecodedClipboardPayload"]


@dataclass(frozen=True, slots=True)
class DecodedClipboardPayload:
    """``image`` is straight-alpha RGBA8 (H x W x 4); ``mask`` is uint8 (H x W)."""

    kind: str
    image: NDArray[np.uint8]
    mask: NDArray[np.uint8]
    bounds: tuple[int, int, int, int]
    source_dpi: float | None
    origin: str  # "loomstroke" (custom MIME) or "image" (standard clipboard image)
    warnings: tuple[str, ...] = ()

    @property
    def size(self) -> tuple[int, int]:
        return int(self.image.shape[1]), int(self.image.shape[0])

    @property
    def is_editable(self) -> bool:
        return self.origin == "loomstroke"
