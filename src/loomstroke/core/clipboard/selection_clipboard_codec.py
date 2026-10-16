"""Selection <-> clipboard envelope + PNG parts (CT-19 spec 19 §8 / Phase B).

Encode: straight RGBA8 image and uint8 mask become two PNG parts plus a
JSON envelope (bounds / DPI / part hashes).  Decode: validate the parts,
decode the PNGs and check they agree with the envelope; a standard
image (no custom MIME) decodes with a full mask derived from its alpha.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from io import BytesIO

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from .clipboard_envelope import ClipboardEnvelope
from .clipboard_mime import ClipboardMime
from .clipboard_payload_validator import ClipboardPayloadValidator
from .decoded_clipboard_payload import DecodedClipboardPayload

__all__ = ["SelectionClipboardCodec"]


class SelectionClipboardCodec:
    def __init__(self, validator: ClipboardPayloadValidator | None = None) -> None:
        self._validator = validator or ClipboardPayloadValidator()

    # -- helpers -------------------------------------------------------------------

    @staticmethod
    def png_bytes(array: NDArray[np.uint8], mode: str) -> bytes:
        buffer = BytesIO()
        Image.fromarray(np.ascontiguousarray(array), mode=mode).save(buffer, format="PNG")
        return buffer.getvalue()

    @staticmethod
    def decode_png(data: bytes, mode: str) -> NDArray[np.uint8]:
        with Image.open(BytesIO(data)) as image:
            image.load()
            return np.asarray(image.convert(mode), dtype=np.uint8).copy()

    # -- encode --------------------------------------------------------------------

    def encode(
        self,
        image: NDArray[np.uint8],
        mask: NDArray[np.uint8] | None,
        *,
        bounds: tuple[int, int, int, int],
        source_dpi: float | None = None,
        canvas_size: tuple[int, int] | None = None,
    ) -> tuple[bytes, dict[str, bytes]]:
        """``(envelope_json, {mime: bytes})`` ready for ``ClipboardProvider.publish``."""
        rgba = np.ascontiguousarray(np.asarray(image, dtype=np.uint8))
        if rgba.ndim != 3 or rgba.shape[2] != 4:
            raise ValueError("image must be RGBA8 (H x W x 4)")
        if mask is None:
            mask_u8 = np.full(rgba.shape[:2], 255, dtype=np.uint8)
        else:
            mask_u8 = np.ascontiguousarray(np.asarray(mask, dtype=np.uint8))
            if mask_u8.shape != rgba.shape[:2]:
                raise ValueError("mask must match the image size")
        image_png = self.png_bytes(rgba, "RGBA")
        mask_png = self.png_bytes(mask_u8, "L")
        parts = (
            (
                "image",
                ClipboardMime.SELECTION_IMAGE,
                len(image_png),
                hashlib.sha256(image_png).hexdigest(),
            ),
            (
                "mask",
                ClipboardMime.SELECTION_MASK,
                len(mask_png),
                hashlib.sha256(mask_png).hexdigest(),
            ),
        )
        x, y, _w, _h = bounds
        envelope = ClipboardEnvelope(
            kind="raster_selection",
            bounds=(int(x), int(y), int(rgba.shape[1]), int(rgba.shape[0])),
            source_dpi=source_dpi,
            canvas_size=canvas_size,
            parts=parts,
        )
        return envelope.to_json(), {
            ClipboardMime.SELECTION_IMAGE: image_png,
            ClipboardMime.SELECTION_MASK: mask_png,
        }

    # -- decode --------------------------------------------------------------------

    def decode(self, envelope_json: bytes, parts: Mapping[str, bytes]) -> DecodedClipboardPayload:
        """Raises ``ValueError(reason_code)`` on any contract violation."""
        envelope = ClipboardEnvelope.from_json(envelope_json)
        image_part = envelope.part("image")
        mask_part = envelope.part("mask")
        if image_part is None or mask_part is None:
            raise ValueError("clipboard_parts_missing")
        image_bytes = parts.get(image_part[0])
        mask_bytes = parts.get(mask_part[0])
        if image_bytes is None or mask_bytes is None:
            raise ValueError("clipboard_parts_missing")
        for data, (_mime, length, digest) in ((image_bytes, image_part), (mask_bytes, mask_part)):
            problem = self._validator.check_part(
                data, declared_length=length, declared_sha256=digest
            )
            if problem:
                raise ValueError(problem)
        x, y, width, height = envelope.bounds
        problem = self._validator.check_dimensions(width, height)
        if problem:
            raise ValueError(problem)
        image = self.decode_png(image_bytes, "RGBA")
        mask = self.decode_png(mask_bytes, "L")
        if image.shape[:2] != (height, width) or mask.shape != (height, width):
            raise ValueError("clipboard_dimensions_mismatch")
        return DecodedClipboardPayload(
            kind=envelope.kind,
            image=image,
            mask=mask,
            bounds=(x, y, width, height),
            source_dpi=envelope.source_dpi,
            origin="loomstroke",
        )

    def decode_image(self, png: bytes) -> DecodedClipboardPayload:
        """Standard clipboard image -> raster selection at the origin."""
        problem = self._validator.check_png(png)
        if problem:
            raise ValueError(problem)
        image = self.decode_png(png, "RGBA")
        height, width = image.shape[:2]
        problem = self._validator.check_dimensions(width, height)
        if problem:
            raise ValueError(problem)
        mask = np.where(image[:, :, 3] > 0, 255, 0).astype(np.uint8)
        if not mask.any():
            mask = np.full((height, width), 255, dtype=np.uint8)
        return DecodedClipboardPayload(
            kind="raster_selection",
            image=image,
            mask=mask,
            bounds=(0, 0, width, height),
            source_dpi=None,
            origin="image",
        )
