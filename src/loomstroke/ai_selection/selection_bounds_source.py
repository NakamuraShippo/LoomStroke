"""``selection_bounds`` source: visible composite limited to the selection AABB (+ padding).

The returned image keeps the canvas size (pixels outside the padded bounds are
transparent) so every existing mask adapter keeps its 1:1 canvas mapping; the
crop bounds and padding are reported in the mapping for the request metadata.
"""

from __future__ import annotations

from typing import Any, ClassVar

from .sam3_source_mapping import Sam3SourceMapping

__all__ = ["SelectionBoundsSourceResolver"]


class SelectionBoundsSourceResolver:
    MAX_PADDING: ClassVar[int] = 512

    def __init__(self, error_type: type[Exception]) -> None:
        self._error = error_type

    def selection_bounds(self, canvas: object) -> tuple[int, int, int, int]:
        bounds = self._raw_bounds(canvas)
        if bounds is None:
            raise self._error("selection_missing", "SAM3 selection_bounds scope needs a selection.")
        x, y, width, height = bounds
        if width <= 0 or height <= 0:
            raise self._error("selection_empty", "SAM3 selection_bounds scope: selection is empty.")
        return (int(x), int(y), int(width), int(height))

    def resolve(
        self, canvas: object, composite: Any, *, padding: int = 0
    ) -> tuple[Any, Sam3SourceMapping]:
        from PySide6.QtCore import QPointF, QRect, Qt  # noqa: PLC0415
        from PySide6.QtGui import QImage, QPainter  # noqa: PLC0415

        x, y, width, height = self.selection_bounds(canvas)
        pad = max(0, min(self.MAX_PADDING, int(padding)))
        canvas_w, canvas_h = int(composite.width()), int(composite.height())
        crop = QRect(x - pad, y - pad, width + 2 * pad, height + 2 * pad).intersected(
            QRect(0, 0, canvas_w, canvas_h)
        )
        if crop.isEmpty():
            raise self._error(
                "selection_outside_canvas", "SAM3 selection_bounds scope: selection is off-canvas."
            )
        limited = QImage(canvas_w, canvas_h, QImage.Format.Format_ARGB32_Premultiplied)
        limited.fill(Qt.GlobalColor.transparent)
        painter = QPainter(limited)
        try:
            painter.drawImage(QPointF(crop.x(), crop.y()), composite.copy(crop))
        finally:
            painter.end()
        mapping = Sam3SourceMapping(
            0,
            0,
            canvas_w,
            canvas_h,
            1.0,
            pad,
            (crop.x(), crop.y(), crop.width(), crop.height()),
        )
        return limited, mapping

    @staticmethod
    def _raw_bounds(canvas: object) -> tuple[int, int, int, int] | None:
        getter = getattr(canvas, "get_selection_bounds", None)
        if callable(getter):
            try:
                rect = getter()
            except Exception:
                rect = None
            if rect is not None and hasattr(rect, "width"):
                return (int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height()))
            if isinstance(rect, (tuple, list)) and len(rect) == 4:
                return (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
        raw = getattr(canvas, "_selection_bounds", None)
        if isinstance(raw, (tuple, list)) and len(raw) == 4:
            return (int(raw[0]), int(raw[1]), int(raw[2]), int(raw[3]))
        selection_getter = getattr(canvas, "get_selection", None)
        if callable(selection_getter):
            try:
                selection = selection_getter()
            except Exception:
                selection = None
            bounds = getattr(selection, "bounds", None)
            if isinstance(bounds, (tuple, list)) and len(bounds) == 4:
                return (int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3]))
        return None
