"""Selection snapshot helpers."""

from __future__ import annotations

from typing import Any, cast

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter

from loomstroke.core.canvas_models import SelectionStateSnapshot
from loomstroke.core.selection_clipboard import SelectionPayload
from loomstroke.host_api import Selection

from .events import Events


class SelectionStateService:
    """Coordinate canvas selection state helpers."""

    def __init__(self, canvas: Any) -> None:
        self._canvas = canvas

    def emit_selection_changed(self) -> None:
        """Notify listeners that the selection mask has changed."""
        selection_mask = getattr(self._canvas, "_selection_mask", None)
        selection_bounds = getattr(self._canvas, "_selection_bounds", None)
        has_selection = selection_mask is not None and selection_bounds is not None
        payload: dict[str, Any] = {"has_selection": has_selection}
        if has_selection and selection_bounds is not None:
            bounds = selection_bounds
            payload["bounds"] = (
                bounds.x(),
                bounds.y(),
                bounds.width(),
                bounds.height(),
            )
        bus = self._canvas._event_bus
        if bus is not None:
            emit = getattr(bus, "emit", None)
            if emit:
                emit(Events.SELECTION_CHANGED, payload)

    def clear_selection_state(self) -> None:
        self._canvas._selection_mask = None
        self._canvas._selection_bounds = None
        self._canvas._selection_mask_full = None
        self._canvas._selection_preview_image = None
        self._canvas._selection_preview_bounds = None
        self._canvas._native_selection_preview = None

    def get_selection(self) -> Selection | None:
        """Get current selection if available."""
        selection_mask = getattr(self._canvas, "_selection_mask", None)
        selection_bounds = getattr(self._canvas, "_selection_bounds", None)
        if selection_mask is None or selection_bounds is None:
            return None
        bounds = selection_bounds
        if bounds.isEmpty():
            return None
        return Selection(
            mask_layer_id=self._canvas._selection_id,
            bounds=(bounds.x(), bounds.y(), bounds.width(), bounds.height()),
        )

    def get_selection_mask(self) -> QImage | None:
        """Return a defensive copy of the selection mask."""
        selection_mask = getattr(self._canvas, "_selection_mask", None)
        if selection_mask is None:
            return None
        return cast("QImage", selection_mask).copy()

    def get_selection_bounds(self) -> QRect | None:
        """Return current selection bounds."""
        selection_bounds = getattr(self._canvas, "_selection_bounds", None)
        if selection_bounds is None:
            return None
        return QRect(selection_bounds)

    def get_selection_snapshot(self) -> SelectionPayload | None:
        """Return a snapshot of the current selection (image + mask)."""
        if not self._canvas._layers or self._canvas._active_index >= len(self._canvas._layers):
            return None
        # The active layer itself: indexing the root list would copy from the
        # ancestor group when a nested layer is active.
        layer = self._canvas._get_active_layer()
        if layer is None or layer.image is None or layer.image.isNull():
            return None
        if getattr(layer, "native_cmyk", None) is not None:
            from .native_cmyk.selection_pixels import capture  # noqa: PLC0415

            return capture(self._canvas, layer)
        mask, bounds = self.selection_clip_state()
        if mask is None or bounds is None or bounds.isEmpty():
            return None
        image = layer.image.copy(bounds)
        mask_patch = mask.copy(bounds)
        self.apply_selection_mask_to_image(image, mask_patch)
        return SelectionPayload(
            image=image,
            mask=mask_patch,
            bounds=(bounds.x(), bounds.y(), bounds.width(), bounds.height()),
            source_layer_id=layer.id,
            dpi=self._canvas._dpi,
        )

    def set_selection_mask(
        self,
        mask: QImage | None,
        bounds: tuple[int, int, int, int] | QRect | None,
    ) -> None:
        """Update selection mask state without mutating layer data."""
        if mask is None or (isinstance(mask, QImage) and mask.isNull()):
            self._canvas._selection_mask = None
            self._canvas._selection_bounds = None
            self._canvas._selection_mask_full = None
            return
        if mask.format() != QImage.Format.Format_Alpha8:
            mask = mask.convertToFormat(QImage.Format.Format_Alpha8)
        self._canvas._selection_mask = mask.copy()
        if isinstance(bounds, QRect):
            self._canvas._selection_bounds = QRect(bounds)
        elif bounds is None:
            computed = self.selection_bounds_from_mask(self._canvas._selection_mask)
            if computed is None:
                self._canvas._selection_mask = None
                self._canvas._selection_bounds = None
                return
            self._canvas._selection_bounds = computed
        else:
            x, y, w, h = bounds
            self._canvas._selection_bounds = QRect(int(x), int(y), int(w), int(h))

    def selection_clip_state(self) -> tuple[QImage | None, QRect | None]:
        mask = getattr(self._canvas, "_selection_mask", None)
        bounds = getattr(self._canvas, "_selection_bounds", None)
        if mask is None or bounds is None or bounds.isEmpty():
            return None, None
        return mask, QRect(bounds)

    def selection_snapshot_image(self, mask: Any | None) -> QImage:
        if mask is None:
            return self.empty_selection_mask()
        if isinstance(mask, QImage):
            if mask.format() != QImage.Format.Format_Alpha8:
                mask = mask.convertToFormat(QImage.Format.Format_Alpha8)
            return self.fit_selection_mask(mask)
        if isinstance(mask, np.ndarray):
            return self.numpy_to_selection_mask(mask)
        return self.empty_selection_mask()

    def empty_selection_mask(self) -> QImage:
        image = QImage(
            self._canvas._width,
            self._canvas._height,
            QImage.Format.Format_Alpha8,
        )
        image.fill(0)
        return image

    def fit_selection_mask(self, mask: QImage) -> QImage:
        if mask.width() == self._canvas._width and mask.height() == self._canvas._height:
            return mask.copy()
        image = self.empty_selection_mask()
        painter = QPainter(image)
        painter.drawImage(0, 0, mask)
        painter.end()
        return image

    def numpy_to_selection_mask(self, mask: NDArray[Any]) -> QImage:
        arr = np.ascontiguousarray(mask, dtype=np.uint8)
        if arr.shape != (self._canvas._height, self._canvas._width):
            arr = np.resize(arr, (self._canvas._height, self._canvas._width)).astype(
                np.uint8,
                copy=False,
            )
        bytes_per_line = arr.strides[0]
        qimage = QImage(
            arr.data,
            self._canvas._width,
            self._canvas._height,
            bytes_per_line,
            QImage.Format.Format_Alpha8,
        )
        return qimage.copy()

    def draw_selection_overlay(self, painter: QPainter) -> None:
        """Draw the current selection preview image onto *painter*."""
        if getattr(self._canvas, "_native_selection_preview", None) is not None:
            return
        preview_image = getattr(self._canvas, "_selection_preview_image", None)
        preview_bounds = getattr(self._canvas, "_selection_preview_bounds", None)
        if preview_image is None or preview_bounds is None:
            return
        x, y, _, _ = preview_bounds
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.drawImage(int(x), int(y), preview_image)
        painter.restore()

    @staticmethod
    def selection_bounds_from_mask(mask: QImage | None) -> QRect | None:
        if mask is None or mask.isNull():
            return None
        arr = SelectionStateService.selection_mask_to_numpy(mask)
        ys, xs = np.where(arr > 0)
        if xs.size == 0 or ys.size == 0:
            return None
        min_x = int(xs.min())
        max_x = int(xs.max())
        min_y = int(ys.min())
        max_y = int(ys.max())
        return QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)

    @staticmethod
    def selection_mask_to_numpy(mask: QImage) -> NDArray[Any]:
        image = mask
        if image.format() != QImage.Format.Format_Alpha8:
            image = image.convertToFormat(QImage.Format.Format_Alpha8)
        bytes_per_line = image.bytesPerLine()
        size = bytes_per_line * image.height()
        buf = image.bits()
        if hasattr(buf, "setsize"):
            buf.setsize(size)
            view = buf
        else:
            view = memoryview(buf)
            if view.nbytes < size:
                return np.zeros((image.height(), image.width()), dtype=np.uint8)
            view = view[:size]
        arr = np.frombuffer(view, dtype=np.uint8, count=size)
        arr = arr.reshape((image.height(), bytes_per_line))[:, : image.width()]
        return arr.copy()

    @staticmethod
    def apply_selection_mask_to_image(image: QImage, mask: QImage) -> None:
        if mask.format() != QImage.Format.Format_Alpha8:
            mask = mask.convertToFormat(QImage.Format.Format_Alpha8)
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, mask)
        painter.end()


def build_selection_state_snapshot(canvas: Any) -> SelectionStateSnapshot:
    """Build a deep-copied selection snapshot from a canvas facade."""

    return SelectionStateSnapshot.from_parts(
        selection_id=str(getattr(canvas, "_selection_id", "selection-mask")),
        mask=getattr(canvas, "_selection_mask", None),
        bounds=getattr(canvas, "_selection_bounds", None),
        preview_image=getattr(canvas, "_selection_preview_image", None),
        preview_bounds=getattr(canvas, "_selection_preview_bounds", None),
        mask_full=getattr(canvas, "_selection_mask_full", None),
    )
