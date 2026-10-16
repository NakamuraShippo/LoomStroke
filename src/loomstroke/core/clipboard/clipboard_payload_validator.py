"""Untrusted-input checks for clipboard payloads (CT-19 spec 19 §11 / §13).

Hard limits on bytes and pixel dimensions, PNG signature, declared vs
actual part length and hash, and decoded-dimension agreement with the
envelope bounds.  Every refusal is a stable reason code.
"""

from __future__ import annotations

import hashlib
from typing import ClassVar

__all__ = ["ClipboardPayloadValidator"]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ClipboardPayloadValidator:
    MAX_PART_BYTES: ClassVar[int] = 128 * 1024 * 1024
    MAX_PIXEL_DIM: ClassVar[int] = 16384
    MAX_PIXELS: ClassVar[int] = 16384 * 16384

    def check_part(self, data: bytes, *, declared_length: int, declared_sha256: str) -> str | None:
        """``None`` when the part is acceptable, else a reason code."""
        if len(data) > self.MAX_PART_BYTES:
            return "clipboard_part_too_large"
        if len(data) != declared_length:
            return "clipboard_part_length_mismatch"
        if hashlib.sha256(data).hexdigest() != declared_sha256:
            return "clipboard_part_hash_mismatch"
        if not data.startswith(_PNG_SIGNATURE):
            return "clipboard_part_not_png"
        return None

    def check_png(self, data: bytes) -> str | None:
        if len(data) > self.MAX_PART_BYTES:
            return "clipboard_image_too_large"
        if not data.startswith(_PNG_SIGNATURE):
            return "clipboard_image_not_png"
        return None

    def check_dimensions(self, width: int, height: int) -> str | None:
        if width <= 0 or height <= 0:
            return "clipboard_image_empty"
        if width > self.MAX_PIXEL_DIM or height > self.MAX_PIXEL_DIM:
            return "clipboard_image_too_large"
        if width * height > self.MAX_PIXELS:
            return "clipboard_image_too_large"
        return None
