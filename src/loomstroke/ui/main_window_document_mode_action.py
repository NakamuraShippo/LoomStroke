"""``File > Document Mode…`` (CT-24 D1): opens the mode / capability / coverage dialog."""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from loomstroke.core.print_modes import DocumentColorMode

from .dialogs.document_mode_dialog import DocumentModeDialog

__all__ = ["MainWindowDocumentModeAction"]


class MainWindowDocumentModeAction:
    def __init__(
        self, window: Any, *, dialog_factory: Callable[..., Any] = DocumentModeDialog
    ) -> None:
        self._window = window
        self._dialog_factory = dialog_factory

    def host(self) -> Any:
        return getattr(self._window, "_host_api", None) or getattr(self._window, "host", None)

    def current_mode(self) -> DocumentColorMode:
        canvas = getattr(self.host(), "canvas", None)
        if canvas is not None and hasattr(canvas, "native_cmyk"):
            profile = canvas.native_cmyk().profile()
            if profile is not None:
                native = next(
                    layer.native_cmyk
                    for layer in canvas._flatten_layers()
                    if layer.native_cmyk is not None
                )
                bits = native.inks.dtype.itemsize * 8
                return DocumentColorMode(
                    f"cmyk{bits}",
                    sha256(profile).hexdigest(),
                    compositing_policy="native_cmyk",
                )
        return DocumentColorMode.default()

    def canvas_size(self) -> tuple[int, int]:
        canvas = getattr(self.host(), "canvas", None)
        width = int(getattr(canvas, "_width", 0) or 0) or 1
        height = int(getattr(canvas, "_height", 0) or 0) or 1
        return (width, height)

    def layer_count(self) -> int:
        canvas = getattr(self.host(), "canvas", None)
        getter = getattr(canvas, "get_layers", None)
        try:
            return len(getter()) if callable(getter) else 1
        except Exception:
            return 1

    def composite_rgba(self) -> np.ndarray | None:
        canvas = getattr(self.host(), "canvas", None)
        getter = getattr(canvas, "get_composited_pixmap", None)
        if not callable(getter):
            return None
        try:
            pixmap = getter()
        except Exception:
            return None
        image = pixmap.toImage() if pixmap is not None and hasattr(pixmap, "toImage") else None
        if not isinstance(image, QImage) or image.isNull():
            return None
        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = converted.width(), converted.height()
        stride = converted.bytesPerLine()
        buffer = np.frombuffer(converted.constBits(), dtype=np.uint8, count=stride * height)
        return buffer.reshape(height, stride // 4, 4)[:, :width].copy()

    def page_id(self) -> str:
        pages = getattr(self.host(), "pages", None)
        getter = getattr(pages, "get_active_page_id", None)
        try:
            return str(getter() or "") if callable(getter) else ""
        except Exception:
            return ""

    def run(self) -> bool:
        dialog = self._dialog_factory(
            current_mode=self.current_mode(),
            canvas_size=self.canvas_size(),
            layer_count=self.layer_count(),
            composite_provider=self.composite_rgba,
            page_id=self.page_id(),
            parent=self._window,
        )
        attach = getattr(dialog, "attach_native_controls", None)
        if callable(attach):
            print_action = getattr(self._window, "_print_export_action", None)
            attach(
                getattr(self.host(), "canvas", None),
                getattr(self.host(), "pages", None),
                getattr(print_action, "registry", None),
                source_profile=(
                    getattr(getattr(self.host(), "canvas", None), "color_profile", None).data
                    if getattr(getattr(self.host(), "canvas", None), "color_profile", None)
                    is not None
                    else None
                ),
            )
        dialog.exec()
        return True
