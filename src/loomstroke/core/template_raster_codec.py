"""Raster artwork inside page templates (CT-07 T2, spec 07 §5.2 / §11).

The template library is JSON (``page_templates.json``), so captured
artwork travels as PNG bytes (base64 in the payload).  This codec owns
that encoding, the 256 px preview composite the template list shows,
and the size estimate the capture dialog warns with — all pure numpy /
Pillow, no Qt.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable
from io import BytesIO
from typing import ClassVar

import numpy as np
from numpy.typing import NDArray
from PIL import Image

__all__ = ["TemplateRasterCodec"]


class TemplateRasterCodec:
    PREVIEW_MAX_PX: ClassVar[int] = 256
    #: Above this the capture dialog warns (raster lives inside the JSON library).
    WARN_BYTES: ClassVar[int] = 8 * 1024 * 1024

    # -- layer artwork ------------------------------------------------------------

    @staticmethod
    def has_artwork(image: NDArray[np.uint8] | None) -> bool:
        array = np.asarray(image) if image is not None else None
        if array is None or array.ndim != 3 or array.shape[2] < 4 or array.size == 0:
            return False
        return bool(np.any(array[:, :, 3]))

    @staticmethod
    def encode(image: NDArray[np.uint8]) -> bytes:
        array = np.ascontiguousarray(np.asarray(image, dtype=np.uint8))
        buffer = BytesIO()
        Image.fromarray(array, mode="RGBA").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    @staticmethod
    def decode(payload: bytes) -> NDArray[np.uint8]:
        with Image.open(BytesIO(payload)) as image:
            return np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()

    @staticmethod
    def to_base64(payload: bytes) -> str:
        return base64.b64encode(payload).decode("ascii")

    @staticmethod
    def from_base64(text: str) -> bytes | None:
        try:
            return base64.b64decode(str(text).encode("ascii"), validate=True)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def fit_to_canvas(image: NDArray[np.uint8], width: int, height: int) -> NDArray[np.uint8]:
        """Place ``image`` at the canvas origin (crop / pad to ``width`` x ``height``)."""
        canvas = np.zeros((max(1, height), max(1, width), 4), dtype=np.uint8)
        source = np.asarray(image, dtype=np.uint8)
        rows = min(canvas.shape[0], source.shape[0])
        cols = min(canvas.shape[1], source.shape[1])
        canvas[:rows, :cols] = source[:rows, :cols]
        return canvas

    # -- estimates / preview --------------------------------------------------------

    @classmethod
    def estimate_bytes(cls, images: Iterable[NDArray[np.uint8] | None]) -> int:
        """Uncompressed upper bound (w*h*4) of the layers that carry artwork."""
        total = 0
        for image in images:
            if cls.has_artwork(image):
                array = np.asarray(image)
                total += int(array.shape[0]) * int(array.shape[1]) * 4
        return total

    @classmethod
    def preview_png(
        cls,
        images: Iterable[NDArray[np.uint8] | None],
        *,
        background: tuple[int, int, int, int] = (255, 255, 255, 255),
    ) -> bytes | None:
        """Bottom-to-top straight-alpha composite, downscaled to 256 px."""
        composite: Image.Image | None = None
        for image in images:
            if not cls.has_artwork(image):
                continue
            layer = Image.fromarray(np.ascontiguousarray(np.asarray(image, dtype=np.uint8)), "RGBA")
            if composite is None:
                composite = Image.new("RGBA", layer.size, background)
            if layer.size != composite.size:
                layer = layer.crop((0, 0, *composite.size))
            composite = Image.alpha_composite(composite, layer)
        if composite is None:
            return None
        composite.thumbnail((cls.PREVIEW_MAX_PX, cls.PREVIEW_MAX_PX))
        buffer = BytesIO()
        composite.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
