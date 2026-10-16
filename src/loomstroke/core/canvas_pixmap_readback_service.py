"""Pixmap readback bridge helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import logging
from typing import Any, cast

from PySide6.QtGui import QPixmap

from loomstroke.core.canvas_composited_pixmap_state_service import (
    CanvasCompositedPixmapStateService,
)

logger = logging.getLogger("loomstroke.core.canvas_service_simple")


class CanvasPixmapReadbackService:
    """Bridge composited pixmap requests to the attached canvas widget."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def attach_canvas_widget(self, widget: Any) -> None:
        """Register the CanvasWidget used for GPU readback."""

        self._owner._canvas_widget = widget

    def get_composited_pixmap(self) -> QPixmap:
        """Return the latest composited pixmap, using GPU readback when available."""

        owner = self._owner
        widget = owner._canvas_widget
        if widget is not None:
            flush = getattr(widget, "flush_and_readback", None)
            if callable(flush):
                try:
                    pixmap = flush()
                except Exception:
                    logger.exception("get_composited_pixmap: flush_and_readback failed")
                    pixmap = None
                if pixmap is not None and not pixmap.isNull():
                    return CanvasCompositedPixmapStateService.store_composited_pixmap(
                        owner,
                        cast("QPixmap", pixmap),
                    )
        return cast("QPixmap", owner._composited)
