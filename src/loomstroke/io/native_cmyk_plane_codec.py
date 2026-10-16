"""Lossless grayscale PNG planes with header checks before allocation."""

from __future__ import annotations

import hashlib
from io import BytesIO

import numpy as np
from PIL import Image

from .lstroke_archive_policy import png_dimensions_from_header


class NativeCmykPlaneCodec:
    @staticmethod
    def encode(plane: np.ndarray) -> bytes:
        if plane.ndim != 2 or plane.dtype not in (np.uint8, np.uint16):
            raise ValueError("A channel plane must be grayscale 8/16-bit")
        output = BytesIO()
        Image.fromarray(np.ascontiguousarray(plane)).save(output, format="PNG")
        return output.getvalue()

    @staticmethod
    def decode(payload: bytes, *, width: int, height: int, bits: int, digest: str) -> np.ndarray:
        dimensions = png_dimensions_from_header(payload[:33], "native channel")
        if (
            len(payload) < 33
            or payload[8:12] != b"\x00\x00\x00\x0d"
            or dimensions != (width, height)
            or payload[24] != bits
            or payload[25] != 0
            or bits not in (8, 16)
            or payload[26:28] != b"\x00\x00"
            or width * height * (bits // 8) > 256 * 1024 * 1024
        ):
            raise ValueError("Native channel PNG header does not match its descriptor")
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("Native channel digest mismatch")
        with Image.open(BytesIO(payload)) as image:
            values = np.asarray(image, dtype=np.uint8 if bits == 8 else np.uint16).copy()
        if values.shape != (height, width):
            raise ValueError("Native channel is not a grayscale plane")
        return values
