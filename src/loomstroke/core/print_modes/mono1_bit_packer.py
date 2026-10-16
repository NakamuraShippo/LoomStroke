"""Deterministic 1-bit plane packing (CT-24 ``mono1``): threshold or ordered 4x4 dither."""

from __future__ import annotations

from typing import ClassVar, Literal

import numpy as np

__all__ = ["Mono1BitPacker"]

DitherMode = Literal["threshold", "ordered4x4"]

_BAYER_4X4 = (
    np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], dtype=np.float32) + 0.5
) / 16.0


class Mono1BitPacker:
    DEFAULT_THRESHOLD: ClassVar[int] = 128

    def binarize(
        self,
        gray: np.ndarray,
        *,
        threshold: int = DEFAULT_THRESHOLD,
        mode: DitherMode = "threshold",
    ) -> np.ndarray:
        """``(h, w)`` uint8 gray -> ``(h, w)`` bool (True = ink / black)."""
        plane = np.asarray(gray)
        if plane.ndim != 2:
            raise ValueError("gray plane must be 2-D")
        level = max(0, min(255, int(threshold)))
        if mode == "ordered4x4":
            h, w = plane.shape
            tile = np.tile(_BAYER_4X4, (h // 4 + 1, w // 4 + 1))[:h, :w]
            limit = (tile * 255.0).astype(np.float32) * (level / 128.0)
            return plane.astype(np.float32) < limit
        return plane < level

    @staticmethod
    def pack(bits: np.ndarray) -> tuple[bytes, int]:
        """Row-major MSB-first packing; returns ``(payload, stride_bytes)``."""
        plane = np.asarray(bits, dtype=np.uint8)
        if plane.ndim != 2:
            raise ValueError("bit plane must be 2-D")
        packed = np.packbits(plane, axis=1, bitorder="big")
        return packed.tobytes(), int(packed.shape[1])

    @staticmethod
    def unpack(payload: bytes, width: int, height: int) -> np.ndarray:
        stride = (int(width) + 7) // 8
        expected = stride * int(height)
        if len(payload) < expected:
            raise ValueError("mono1 payload truncated")
        rows = np.frombuffer(payload, dtype=np.uint8, count=expected).reshape(int(height), stride)
        return np.unpackbits(rows, axis=1, bitorder="big")[:, : int(width)].astype(bool)

    @staticmethod
    def to_gray(bits: np.ndarray) -> np.ndarray:
        return np.where(np.asarray(bits, dtype=bool), 0, 255).astype(np.uint8)
