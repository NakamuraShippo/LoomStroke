"""Internal selection clipboard <-> OS clipboard (CT-19 spec 19 §6 / §9 / §14).

Publish: after a Copy/Cut the internal :class:`SelectionPayload` is
encoded as the LoomStroke envelope + PNG parts and a flattened standard
image (image-only fallback when the editable publish fails).  Paste: the
preferred OS offer is decoded and applied through the existing
``PasteSelectionCommand`` onto a new layer — one undo step, captured
bytes only (Undo/Redo never re-read the clipboard).  Nothing about the
clipboard content is logged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtGui import QImage

from ..core.clipboard.clipboard_mime import ClipboardMime
from ..core.clipboard.clipboard_paste_planner import ClipboardPastePlanner
from ..core.clipboard.clipboard_provider import ClipboardProvider
from ..core.clipboard.decoded_clipboard_payload import DecodedClipboardPayload
from ..core.clipboard.selection_clipboard_codec import SelectionClipboardCodec
from ..core.commands_selection_content import PasteSelectionCommand
from ..core.commands_selection_mask import UpdateSelectionMaskCommand
from ..core.selection_clipboard import SelectionPayload

logger = logging.getLogger(__name__)

__all__ = ["SystemClipboardBridge"]


class SystemClipboardBridge:
    PASTE_LAYER_NAME: str = "Pasted Layer"

    def __init__(
        self,
        host_api_getter: Callable[[], Any],
        provider: ClipboardProvider,
        *,
        codec: SelectionClipboardCodec | None = None,
    ) -> None:
        self._host = host_api_getter
        self._provider = provider
        self._codec = codec or SelectionClipboardCodec()
        self._planner = ClipboardPastePlanner(provider, self._codec)

    # -- publish -------------------------------------------------------------------

    def publish_internal_selection(self) -> str:
        """``published`` / ``published_image_only`` / ``nothing`` / ``failed``."""
        host = self._host()
        clipboard = getattr(host, "selection_clipboard", None) if host else None
        payload = clipboard.get() if clipboard is not None and not clipboard.is_empty() else None
        if payload is None:
            return "nothing"
        image = self._image_to_rgba(payload.image)
        if image is None:
            return "nothing"
        mask = self._mask_to_u8(payload.mask, image.shape[1], image.shape[0])
        image_png = self._codec.png_bytes(image, "RGBA")
        try:
            envelope, parts = self._codec.encode(
                image,
                mask,
                bounds=tuple(int(v) for v in payload.bounds),  # type: ignore[arg-type]
                source_dpi=float(payload.dpi) if payload.dpi else None,
                canvas_size=self._canvas_size(),
            )
            if self._provider.publish(
                {ClipboardMime.SELECTION: envelope, **parts}, image_png=image_png
            ):
                return "published"
        except Exception:
            logger.debug("editable clipboard publish failed; trying image only", exc_info=True)
        try:
            if self._provider.publish({}, image_png=image_png):
                return "published_image_only"
        except Exception:
            logger.debug("image-only clipboard publish failed", exc_info=True)
        return "failed"

    # -- paste ---------------------------------------------------------------------

    def paste_from_system(self) -> tuple[bool, str]:
        """Decode the preferred OS offer and submit a paste command.

        Returns ``(pasted, reason_code)``; reasons on failure are stable
        codes (``clipboard_unsupported`` ...), on success ``ok`` or the
        fallback warning when the custom payload was invalid.
        """
        decoded, reason = self._planner.decode_preferred()
        if decoded is None:
            return False, reason
        host = self._host()
        canvas = getattr(host, "canvas", None) if host else None
        submit = getattr(canvas, "submit", None)
        if canvas is None or not callable(submit):
            return False, "canvas_unavailable"
        layer_id = self._create_paste_layer(canvas)
        command = PasteSelectionCommand(
            self._selection_payload(decoded),
            position=self._paste_position(canvas, decoded),
            layer_id=layer_id,
        )
        try:
            accepted = bool(submit(command))
        except Exception:
            logger.warning("system clipboard paste failed", exc_info=True)
            accepted = False
        if not accepted:
            self._remove_layer(canvas, layer_id)
            return False, "clipboard_paste_rejected"
        return True, reason

    # -- helpers -------------------------------------------------------------------

    def _canvas_size(self) -> tuple[int, int] | None:
        host = self._host()
        canvas = getattr(host, "canvas", None) if host else None
        width = int(getattr(canvas, "_width", 0) or 0)
        height = int(getattr(canvas, "_height", 0) or 0)
        return (width, height) if width > 0 and height > 0 else None

    @staticmethod
    def _selection_payload(decoded: DecodedClipboardPayload) -> SelectionPayload:
        return SelectionPayload(
            image=np.ascontiguousarray(decoded.image),
            mask=np.ascontiguousarray(decoded.mask),
            bounds=decoded.bounds,
            source_layer_id=None,
            dpi=int(decoded.source_dpi) if decoded.source_dpi else None,
        )

    def _paste_position(self, canvas: Any, decoded: DecodedClipboardPayload) -> tuple[int, int]:
        size = self._canvas_size() or (0, 0)
        width, height = decoded.size
        if size == (0, 0):
            return (0, 0)
        return (max(0, (size[0] - width) // 2), max(0, (size[1] - height) // 2))

    def _create_paste_layer(self, canvas: Any) -> str | None:
        add_layer = getattr(canvas, "add_layer", None)
        if not callable(add_layer):
            return None
        try:
            ref = add_layer(self.PASTE_LAYER_NAME)
        except Exception:
            return None
        layer_id = getattr(ref, "id", None)
        return str(layer_id) if layer_id else None

    @staticmethod
    def _remove_layer(canvas: Any, layer_id: str | None) -> None:
        if not layer_id:
            return
        remove = getattr(canvas, "remove_layer", None)
        if callable(remove):
            try:
                remove(layer_id)
            except Exception:
                logger.debug("paste layer cleanup failed", exc_info=True)

    @staticmethod
    def _image_to_rgba(image: Any) -> NDArray[np.uint8] | None:
        if isinstance(image, np.ndarray):
            array = np.asarray(image, dtype=np.uint8)
            if array.ndim == 3 and array.shape[2] == 4:
                return np.ascontiguousarray(array)
            return None
        if isinstance(image, QImage) and not image.isNull():
            converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
            width, height = converted.width(), converted.height()
            raw = np.frombuffer(bytes(converted.constBits()), dtype=np.uint8)
            rows = raw.reshape(height, converted.bytesPerLine())
            return np.ascontiguousarray(rows[:, : width * 4].reshape(height, width, 4))
        return None

    @staticmethod
    def _mask_to_u8(mask: Any, width: int, height: int) -> NDArray[np.uint8]:
        if isinstance(mask, np.ndarray) and mask.shape[:2] == (height, width):
            return np.ascontiguousarray(mask.astype(np.uint8, copy=False))
        try:
            converted = UpdateSelectionMaskCommand._mask_to_numpy(mask, width, height)
            array = np.asarray(converted, dtype=np.uint8)
            if array.shape[:2] == (height, width):
                return np.ascontiguousarray(array)
        except Exception:
            logger.debug("clipboard mask conversion failed", exc_info=True)
        return np.full((height, width), 255, dtype=np.uint8)
