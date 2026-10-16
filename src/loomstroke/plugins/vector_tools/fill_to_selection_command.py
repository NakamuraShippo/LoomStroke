"""Command to convert vector fill area into a raster selection mask."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath

from ...core.commands import Command, DirtyRegion, SelectionOp


class VectorFillToSelectionCommand(Command):
    """Convert closed vector paths into a raster selection mask."""

    def __init__(
        self,
        layer_id: str,
        vector_id: str,
        operation: SelectionOp | str = SelectionOp.REPLACE,
        *,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self._layer_id = layer_id
        self._vector_id = vector_id
        self._operation = (
            operation if isinstance(operation, SelectionOp) else SelectionOp(operation)
        )
        self._before_mask: QImage | None = None
        self._before_bounds: tuple[int, int, int, int] | None = None

    def apply(self, canvas: Any) -> bool:
        width, height = self._canvas_size(canvas)
        if width <= 0 or height <= 0:
            return False

        vec = self._find_vector_object(canvas)
        if vec is None:
            return False

        # Build QPainterPath list from the vector object
        qpaths = self._build_qpaths(vec)
        if not qpaths:
            return False

        # Render fill areas to a mask
        new_mask = self._render_fill_mask(qpaths, width, height)
        if new_mask is None or not np.any(new_mask):
            return False

        # Save current selection for undo
        self._before_mask, self._before_bounds = self._read_selection(canvas)

        # Compose with existing selection
        old_mask = self._selection_to_numpy(self._before_mask, width, height)
        composed = self._compose(old_mask, new_mask, self._operation)

        if not np.any(composed):
            self._set_selection(canvas, None, None)
        else:
            bounds = self._mask_bounds(composed)
            qmask = self._numpy_to_qimage(composed)
            self._set_selection(canvas, qmask, bounds)

        self._dirty_region = DirtyRegion(0, 0, width, height)
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        width, height = self._canvas_size(canvas)
        self._set_selection(canvas, self._before_mask, self._before_bounds)
        self._dirty_region = DirtyRegion(0, 0, width, height)
        self._executed = False
        return True

    # --- helpers ---

    def _find_vector_object(self, canvas: Any) -> Any:
        """Find the target vector object in the canvas layers."""
        layers = getattr(canvas, "_layers", [])
        for layer in layers:
            if getattr(layer, "id", None) != self._layer_id:
                continue
            if getattr(layer, "layer_type", None) != "vector":
                continue
            for obj in getattr(layer, "vector_objects", []):
                if getattr(obj, "id", None) == self._vector_id:
                    return obj
        return None

    @staticmethod
    def _build_qpaths(vec: Any) -> list[QPainterPath]:
        """Build QPainterPath list from a VectorObject (closed paths only)."""
        result: list[QPainterPath] = []
        paths = getattr(vec, "paths", None) or []
        for path in paths:
            anchors = getattr(path, "anchors", None) or []
            if len(anchors) < 3:
                continue

            # Check if path is closed (explicitly or visually)
            path_closed = getattr(path, "closed", False)
            if not path_closed:
                fa = anchors[0].pos
                la = anchors[-1].pos
                if (fa[0] - la[0]) ** 2 + (fa[1] - la[1]) ** 2 < 0.25:
                    path_closed = True
            if not path_closed:
                continue

            qpath = QPainterPath()
            first = anchors[0]
            qpath.moveTo(float(first.pos[0]), float(first.pos[1]))
            for i in range(len(anchors) - 1):
                a = anchors[i]
                b = anchors[i + 1]
                c1 = (
                    a.pos[0] + (a.h_out[0] if a.h_out else 0.0),
                    a.pos[1] + (a.h_out[1] if a.h_out else 0.0),
                )
                c2 = (
                    b.pos[0] + (b.h_in[0] if b.h_in else 0.0),
                    b.pos[1] + (b.h_in[1] if b.h_in else 0.0),
                )
                has_out = a.h_out and (abs(a.h_out[0]) > 1e-3 or abs(a.h_out[1]) > 1e-3)
                has_in = b.h_in and (abs(b.h_in[0]) > 1e-3 or abs(b.h_in[1]) > 1e-3)
                if has_out or has_in:
                    qpath.cubicTo(c1[0], c1[1], c2[0], c2[1], float(b.pos[0]), float(b.pos[1]))
                else:
                    qpath.lineTo(float(b.pos[0]), float(b.pos[1]))

            # Close back to first anchor
            last = anchors[-1]
            first_anchor = anchors[0]
            c1 = (
                last.pos[0] + (last.h_out[0] if last.h_out else 0.0),
                last.pos[1] + (last.h_out[1] if last.h_out else 0.0),
            )
            c2 = (
                first_anchor.pos[0] + (first_anchor.h_in[0] if first_anchor.h_in else 0.0),
                first_anchor.pos[1] + (first_anchor.h_in[1] if first_anchor.h_in else 0.0),
            )
            has_out = last.h_out and (abs(last.h_out[0]) > 1e-3 or abs(last.h_out[1]) > 1e-3)
            has_in = first_anchor.h_in and (
                abs(first_anchor.h_in[0]) > 1e-3 or abs(first_anchor.h_in[1]) > 1e-3
            )
            if has_out or has_in:
                qpath.cubicTo(
                    c1[0],
                    c1[1],
                    c2[0],
                    c2[1],
                    float(first_anchor.pos[0]),
                    float(first_anchor.pos[1]),
                )
            else:
                qpath.lineTo(float(first_anchor.pos[0]), float(first_anchor.pos[1]))
            qpath.closeSubpath()
            result.append(qpath)
        return result

    @staticmethod
    def _render_fill_mask(qpaths: list[QPainterPath], width: int, height: int) -> np.ndarray | None:
        """Render closed QPainterPaths as a white-on-black mask."""
        img = QImage(width, height, QImage.Format.Format_Grayscale8)
        img.fill(Qt.GlobalColor.black)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.GlobalColor.white)
        for qpath in qpaths:
            painter.drawPath(qpath)
        painter.end()

        # Convert QImage to numpy
        bpl = img.bytesPerLine()
        size = bpl * height
        bits = img.bits()
        if hasattr(bits, "setsize"):
            bits.setsize(size)
        arr = np.frombuffer(bits, dtype=np.uint8, count=size).reshape(height, bpl)
        return arr[:, :width].copy()

    @staticmethod
    def _canvas_size(canvas: Any) -> tuple[int, int]:
        w = getattr(canvas, "_width", 0) or getattr(canvas, "width", 0)
        h = getattr(canvas, "_height", 0) or getattr(canvas, "height", 0)
        if callable(w):
            w = w()
        if callable(h):
            h = h()
        return int(w), int(h)

    @staticmethod
    def _read_selection(canvas: Any) -> tuple[QImage | None, tuple[int, int, int, int] | None]:
        getter = getattr(canvas, "get_selection_mask", None)
        mask = getter() if callable(getter) else None
        bg = getattr(canvas, "get_selection_bounds", None)
        bounds = bg() if callable(bg) else None
        if bounds is not None and not isinstance(bounds, tuple):
            from PySide6.QtCore import QRect  # noqa: PLC0415

            if isinstance(bounds, QRect):
                bounds = (bounds.x(), bounds.y(), bounds.width(), bounds.height())
            else:
                bounds = None
        return mask, bounds

    @staticmethod
    def _set_selection(
        canvas: Any,
        mask: QImage | None,
        bounds: tuple[int, int, int, int] | None,
    ) -> None:
        setter = getattr(canvas, "set_selection_mask", None)
        if callable(setter):
            setter(mask, bounds=bounds)

    @staticmethod
    def _selection_to_numpy(mask: QImage | None, width: int, height: int) -> np.ndarray:
        if mask is None:
            return np.zeros((height, width), dtype=np.uint8)
        img = mask.convertToFormat(QImage.Format.Format_Alpha8)
        bits = img.bits()
        if hasattr(bits, "setsize"):
            bits.setsize(img.bytesPerLine() * img.height())
        arr = np.frombuffer(bits, dtype=np.uint8).reshape(img.height(), img.bytesPerLine())
        return arr[:, :width].copy()

    @staticmethod
    def _compose(old: np.ndarray, new: np.ndarray, op: SelectionOp) -> np.ndarray:
        if op == SelectionOp.REPLACE:
            return new
        if op == SelectionOp.ADD:
            return np.maximum(old, new)
        if op == SelectionOp.SUBTRACT:
            return np.clip(old.astype(np.int16) - new.astype(np.int16), 0, 255).astype(np.uint8)
        if op == SelectionOp.INTERSECT:
            return np.minimum(old, new)
        return new

    @staticmethod
    def _mask_bounds(mask: np.ndarray) -> tuple[int, int, int, int] | None:
        ys, xs = np.where(mask > 0)
        if len(ys) == 0:
            return None
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        return (x0, y0, x1 - x0 + 1, y1 - y0 + 1)

    @staticmethod
    def _numpy_to_qimage(arr: np.ndarray) -> QImage:
        h, w = arr.shape[:2]
        img = QImage(w, h, QImage.Format.Format_Alpha8)
        for y in range(h):
            bits = img.scanLine(y)
            if hasattr(bits, "setsize"):
                bits.setsize(img.bytesPerLine())
            row = np.frombuffer(bits, dtype=np.uint8, count=img.bytesPerLine())
            row[:w] = arr[y, :w]
        return img
