"""Create a new image document from a captured OS clipboard offer."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from ..core.clipboard.clipboard_mime import ClipboardMime
from ..core.clipboard.clipboard_paste_planner import ClipboardPastePlanner
from ..core.clipboard.clipboard_payload_validator import ClipboardPayloadValidator
from ..core.clipboard.decoded_clipboard_payload import DecodedClipboardPayload
from ..core.clipboard.selection_clipboard_codec import SelectionClipboardCodec
from .main_window_edit_shape_actions import _PASTE_MESSAGES
from .qt_clipboard_provider import QtClipboardProvider
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)


def clipboard_image_available(clipboard: Any | None = None) -> bool:
    """Check usable clipboard offers without encoding a standard image."""
    try:
        current = clipboard if clipboard is not None else QApplication.clipboard()
        mime = current.mimeData() if current is not None else None
        if mime is None:
            return False
        validator = ClipboardPayloadValidator()
        if mime.hasImage():
            image = mime.imageData()
            if (
                isinstance(image, QImage)
                and not image.isNull()
                and validator.check_dimensions(image.width(), image.height()) is None
            ):
                return True
        if all(
            mime.hasFormat(format_name)
            for format_name in (
                ClipboardMime.SELECTION,
                ClipboardMime.SELECTION_IMAGE,
                ClipboardMime.SELECTION_MASK,
            )
        ):
            SelectionClipboardCodec(validator).decode(
                bytes(mime.data(ClipboardMime.SELECTION)),
                {
                    format_name: bytes(mime.data(format_name))
                    for format_name in (
                        ClipboardMime.SELECTION_IMAGE,
                        ClipboardMime.SELECTION_MASK,
                    )
                },
            )
            return True
    except Exception:
        logger.debug("Clipboard image availability check failed", exc_info=True)
    return False


def read_clipboard_image(workflow: Any) -> DecodedClipboardPayload | None:
    window = workflow._window
    try:
        decoded, reason = ClipboardPastePlanner(QtClipboardProvider()).decode_preferred()
    except Exception:
        logger.warning("Clipboard document image could not be read", exc_info=True)
        decoded, reason = None, "clipboard_image_decode_failed"
    if decoded is not None:
        return decoded
    workflow._message_box().warning(
        window,
        translate_with_fallback("MainWindow", "New Document Failed"),
        translate_with_fallback(
            "MainWindow", _PASTE_MESSAGES.get(reason, _PASTE_MESSAGES["clipboard_unsupported"])
        ),
    )
    return None


def create_clipboard_document(workflow: Any, decoded: DecodedClipboardPayload) -> bool:
    """Prepare pixels off-document, then commit with the image-open lifecycle."""
    window = workflow._window
    service = window._get_document_service()
    prepare = getattr(service, "_prepare_clipboard_image_document", None)
    commit = getattr(service, "_commit_image_document", None)
    if not callable(prepare) or not callable(commit):
        return _failed(workflow)
    original = workflow._replacement_guard.capture_state()
    capture_pending = workflow._replacement_guard._capture_blocked()
    try:
        image = np.array(decoded.image, copy=True)
        image[:, :, 3] = (
            image[:, :, 3].astype(np.uint16) * decoded.mask.astype(np.uint16) // 255
        ).astype(np.uint8)
        prepared = prepare(image)
        if (
            workflow._replacement_guard.capture_state() != original
            or workflow._replacement_guard._capture_blocked() != capture_pending
        ):
            return False

        def on_committed() -> None:
            window._document_path = None
            window._document_dirty = False
            window._has_document = True
            workflow._emit_document_created(
                {
                    "width": prepared.metadata.width,
                    "height": prepared.metadata.height,
                    "source": "clipboard_image",
                }
            )

        if commit(prepared, on_committed=on_committed) is not True:
            raise ValueError("The clipboard document was not committed")
    except Exception:
        logger.exception("Clipboard document preparation or commit failed")
        return _failed(workflow)
    for update in (
        window._update_window_title,
        lambda: window._set_document_present(True),
        lambda: window.statusBar().showMessage(
            translate_with_fallback("MainWindow", "Created a canvas from the clipboard image."),
            3000,
        ),
    ):
        try:
            update()
        except Exception:
            logger.exception("Clipboard document committed; display update failed")
    return True


def _failed(workflow: Any) -> bool:
    workflow._message_box().critical(
        workflow._window,
        translate_with_fallback("MainWindow", "New Document Failed"),
        translate_with_fallback(
            "MainWindow", "Could not create a canvas from the clipboard image."
        ),
    )
    return False
