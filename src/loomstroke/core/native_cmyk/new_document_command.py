"""Prepared native blank layers at the existing new-document history boundary."""

from __future__ import annotations

import time

import numpy as np
from PIL import ImageCms
from PySide6.QtGui import QColor

from ..events import DocumentChangeEvent
from ..new_document_color_spec import NewDocumentColorSpec
from .buffer import NativeCmykBuffer
from .color_transform import CmykColorTransform
from .pixels import NativeCmykPixels


class NewCmykDocumentCommand:
    """Creation clears history; it never adds an undoable RGB conversion step."""

    def __init__(
        self, width: int, height: int, dpi: int, background: QColor, color: NewDocumentColorSpec
    ):
        if color.mode_id not in ("cmyk8", "cmyk16"):
            raise ValueError("CMYK creation requires a CMYK mode")
        assert color.profile is not None
        self.profile = CmykColorTransform.validate_profile(color.profile)
        self.width, self.height, self.dpi = int(width), int(height), int(dpi)
        self.bits = 16 if color.mode_id == "cmyk16" else 8
        self.background = QColor(background)
        size = self.width * self.height * 5 * (self.bits // 8)
        if min(self.width, self.height, self.dpi) < 1 or size > 256 * 1024 * 1024:
            raise ValueError("CMYK layer exceeds the decoded layer budget")
        self._buffers: list[NativeCmykBuffer] = []

    def prepare(self) -> None:
        """Allocate both layers before touching the current document."""
        if self._buffers:
            return
        try:
            rgba = np.zeros((1, 1, 4), np.uint8)
            inks, alpha = CmykColorTransform.from_rgb(rgba, self.profile)
            CmykColorTransform.to_rgb(inks, alpha, self.profile)
        except (ImageCms.PyCMSError, OSError) as exc:
            raise ValueError("The CMYK profile does not support editing and preview") from exc
        dtype = np.uint16 if self.bits == 16 else np.uint8
        # Existing reset_document creates two transparent layers over the canvas background.
        pixels = NativeCmykPixels(
            np.zeros((self.height, self.width, 4), dtype),
            np.zeros((self.height, self.width), dtype),
            self.profile,
        )
        self._buffers = [NativeCmykBuffer(pixels), NativeCmykBuffer(pixels)]

    def apply(self, canvas) -> bool:
        if not callable(getattr(canvas, "suspend_doc_events", None)):
            raise ValueError("This canvas does not support native CMYK creation")
        if canvas._in_transaction:
            raise ValueError("Finish the current operation before creating a document")
        self.prepare()
        with canvas.suspend_doc_events():
            canvas.reset_document(
                width=self.width, height=self.height, dpi=self.dpi, background=self.background
            )
            for layer, buffer in zip(canvas._layers, self._buffers, strict=True):
                layer.native_cmyk = buffer
                layer.texture_version += 1
            self._buffers = []
            canvas.native_cmyk().set_foreground(None)
            canvas.channels().reset()
            canvas._invalidate_layer_cache()
            canvas._recompose()
        canvas._emit_doc_changed(
            DocumentChangeEvent(
                change_type="new_document",
                affected_area=(0, 0, self.width, self.height),
                layer_id=canvas._get_active_layer().id,
                source="canvas",
                timestamp=time.time(),
            )
        )
        return True
