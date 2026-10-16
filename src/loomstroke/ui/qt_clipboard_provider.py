"""``ClipboardProvider`` over ``QClipboard`` / ``QMimeData`` (CT-19 spec 19 §7).

Multi-part custom payloads ride as separate MIME entries on one
``QMimeData`` (the envelope JSON plus the PNG parts) together with the
standard image, so other applications still see a plain image while
LoomStroke instances recover mask / bounds / DPI.  Reads return copies
of bytes; no Qt object leaves this adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData
from PySide6.QtGui import QImage

__all__ = ["QtClipboardProvider"]


class QtClipboardProvider:
    def __init__(self, clipboard: Callable[[], Any] | None = None) -> None:
        self._clipboard = clipboard or self._application_clipboard
        self._sequence = 0

    @staticmethod
    def _application_clipboard() -> Any:
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415

        return QApplication.clipboard()

    def _mime(self) -> QMimeData | None:
        clipboard = self._clipboard()
        mime = clipboard.mimeData() if clipboard is not None else None
        return mime if isinstance(mime, QMimeData) else None

    def formats(self) -> Sequence[str]:
        mime = self._mime()
        return tuple(str(f) for f in mime.formats()) if mime is not None else ()

    def data(self, mime_type: str) -> bytes | None:
        mime = self._mime()
        if mime is None or not mime.hasFormat(mime_type):
            return None
        return bytes(mime.data(mime_type).data())

    def image_png(self) -> bytes | None:
        mime = self._mime()
        if mime is None or not mime.hasImage():
            return None
        image = mime.imageData()
        if not isinstance(image, QImage) or image.isNull():
            return None
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        ok = image.convertToFormat(QImage.Format.Format_RGBA8888).save(buffer, "PNG")
        buffer.close()
        return bytes(buffer.data().data()) if ok else None

    def publish(self, parts: Mapping[str, bytes], *, image_png: bytes | None) -> bool:
        clipboard = self._clipboard()
        if clipboard is None:
            return False
        mime = QMimeData()
        for mime_type, payload in parts.items():
            mime.setData(mime_type, QByteArray(bytes(payload)))
        if image_png is not None:
            image = QImage()
            if image.loadFromData(image_png, "PNG"):
                mime.setImageData(image)
        try:
            clipboard.setMimeData(mime)
        except Exception:
            return False
        self._sequence += 1
        return True

    def sequence(self) -> int:
        clipboard = self._clipboard()
        # QClipboard has no portable change counter; approximate with a
        # hash of the current format list plus our own publish counter.
        mime = clipboard.mimeData() if clipboard is not None else None
        formats = tuple(str(f) for f in mime.formats()) if mime is not None else ()
        return (hash(formats) & 0xFFFF) * 100_000 + self._sequence if formats else 0
