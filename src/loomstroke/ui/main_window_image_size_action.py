"""``Image > Image Size...`` action for MainWindow (CT-13).

Reads the live canvas geometry, runs :class:`ImageSizeDialog` and applies
the result through the canvas facade ``resample_canvas`` (one undo group),
then syncs the canvas widget like the Canvas Size / rotate actions do.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from PySide6.QtWidgets import QDialog, QMessageBox

from .dialogs.image_size_dialog import ImageSizeDialog

logger = logging.getLogger(__name__)


class MainWindowImageSizeAction:
    """Open the Image Size dialog and resample the active canvas."""

    def __init__(
        self,
        window: Any,
        *,
        dialog_factory: Callable[..., Any] | None = None,
        dialog_type: Callable[[], Any] | None = None,
        message_box: Callable[[], Any] | None = None,
    ) -> None:
        self._window = window
        self._dialog_factory = dialog_factory or ImageSizeDialog
        self._dialog_type = dialog_type or (lambda: QDialog)
        self._message_box = message_box or (lambda: QMessageBox)

    def run(self) -> bool:
        """Return ``True`` when the canvas was changed."""
        canvas = self._canvas_service()
        if canvas is None or not hasattr(canvas, "resample_canvas"):
            self._warn("Canvas service is not available.")
            return False
        width, height = self._canvas_size(canvas)
        dpi = int(getattr(canvas, "_dpi", 96) or 96)
        dialog = self._dialog_factory(
            width=width,
            height=height,
            dpi=dpi,
            layer_count=self._layer_count(canvas),
            parent=self._window,
        )
        if dialog.exec() != self._dialog_type().DialogCode.Accepted:
            return False
        target = dialog.target()
        if (target.width_px, target.height_px, target.dpi) == (width, height, dpi):
            return False
        try:
            ok = bool(
                canvas.resample_canvas(
                    new_width=target.width_px,
                    new_height=target.height_px,
                    dpi=target.dpi,
                    mode=target.interpolation.value,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Image Size failed: %s", exc)
            ok = False
        if not ok:
            self._warn("Failed to resize image.")
            return False
        self._sync_canvas_widget(target.width_px, target.height_px)
        if (target.width_px, target.height_px) != (width, height):
            message = self._window._ts("Image resized to {width}x{height} px at {dpi} dpi").format(
                width=target.width_px, height=target.height_px, dpi=target.dpi
            )
        else:
            message = self._window._ts("Resolution set to {dpi} dpi").format(dpi=target.dpi)
        self._window.statusBar().showMessage(message, 3000)
        return True

    # -- helpers ------------------------------------------------------------------

    def _canvas_service(self) -> Any | None:
        host_api = getattr(self._window, "_host_api", None)
        return getattr(host_api, "canvas", None) if host_api else None

    def _canvas_size(self, canvas: Any) -> tuple[int, int]:
        width = getattr(canvas, "_width", None)
        height = getattr(canvas, "_height", None)
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            return width, height
        current = getattr(self._window, "_current_canvas_size", None)
        if callable(current):
            size = current()
            return max(1, int(size[0])), max(1, int(size[1]))
        return 1, 1

    @staticmethod
    def _layer_count(canvas: Any) -> int:
        flatten = getattr(canvas, "_flatten_layers", None)
        if callable(flatten):
            with suppress(Exception):
                return max(
                    1,
                    len(
                        [layer for layer in flatten() if getattr(layer, "image", None) is not None]
                    ),
                )
        return 1

    def _sync_canvas_widget(self, width: int, height: int) -> None:
        canvas_widget = getattr(self._window, "canvas", None)
        if canvas_widget is not None and hasattr(canvas_widget, "set_canvas_size"):
            with suppress(Exception):
                canvas_widget.set_canvas_size(width, height)

    def _warn(self, source_text: str) -> None:
        self._message_box().warning(
            self._window,
            self._window._ts("Image Size"),
            self._window._ts(source_text),
        )
