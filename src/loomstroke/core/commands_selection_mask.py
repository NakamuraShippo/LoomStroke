"""Selection mask commands and state helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from numpy.typing import NDArray

from .command_types import (
    SELECTION_MASK_LAYER_ID,
    Command,
    DirtyRegion,
    SelectionOp,
    SelectionRect,
    SelectionSnapshot,
)

logger = logging.getLogger("loomstroke.core.commands")

if TYPE_CHECKING:
    from .canvas import Canvas


def _normalize_op(operation: SelectionOp | str) -> SelectionOp:
    if isinstance(operation, SelectionOp):
        return operation
    try:
        return SelectionOp(str(operation))
    except ValueError:
        return SelectionOp.REPLACE


def _canvas_size(canvas: Canvas) -> tuple[int, int]:
    width = getattr(canvas, "_width", None)
    height = getattr(canvas, "_height", None)
    if isinstance(width, int) and isinstance(height, int):
        return width, height
    config = getattr(canvas, "config", None)
    if config is not None and hasattr(config, "width") and hasattr(config, "height"):
        return int(config.width), int(config.height)
    size = getattr(canvas, "size", None)
    if callable(size):
        try:
            w, h = size()
            return int(w), int(h)
        except Exception as exc:
            logger.debug("_canvas_size: canvas.size() call failed: %s", exc)
    return 0, 0


def _normalize_rect(rect: SelectionRect | None, width: int, height: int) -> SelectionRect | None:
    if rect is None:
        return None
    x, y, w, h = rect
    if w <= 0 or h <= 0:
        return None
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(width, int(x + w))
    y1 = min(height, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def _rect_to_mask(rect: SelectionRect | None, width: int, height: int) -> NDArray[Any] | None:
    if rect is None:
        return None
    x, y, w, h = rect
    mask = np.zeros((height, width), dtype=np.uint8)
    x1 = min(width, x + w)
    y1 = min(height, y + h)
    if x1 <= x or y1 <= y:
        return None
    mask[y:y1, x:x1] = 255
    return mask


def _compose_mask(  # noqa: PLR0911
    old: NDArray[Any],
    rect_mask: NDArray[Any] | None,
    op: SelectionOp,
    had_mask: bool,
) -> NDArray[Any] | None:
    if rect_mask is None:
        if op == SelectionOp.REPLACE:
            return None
        if not had_mask:
            return None
        return old.copy()
    if op == SelectionOp.ADD:
        return cast("NDArray[Any] | None", np.maximum(old, rect_mask))
    if op == SelectionOp.SUBTRACT:
        return np.where(rect_mask > 0, 0, old).astype(np.uint8, copy=False)
    if op == SelectionOp.INTERSECT:
        return np.where(rect_mask > 0, old, 0).astype(np.uint8, copy=False)
    return rect_mask.copy()


def _mask_bounds(mask: NDArray[Any] | None) -> SelectionRect | None:
    if mask is None:
        return None
    ys, xs = np.where(mask > 0)
    if xs.size == 0 or ys.size == 0:
        return None
    min_x = int(xs.min())
    max_x = int(xs.max())
    min_y = int(ys.min())
    max_y = int(ys.max())
    return (min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def _mask_to_numpy(mask: Any | None, width: int, height: int) -> NDArray[Any]:  # noqa: PLR0911
    if mask is None:
        return np.zeros((height, width), dtype=np.uint8)
    if isinstance(mask, np.ndarray):
        return mask.astype(np.uint8, copy=False)
    try:
        from PySide6.QtGui import QImage  # noqa: PLC0415
    except ImportError:
        return np.zeros((height, width), dtype=np.uint8)
    if isinstance(mask, QImage):
        if mask.isNull():
            return np.zeros((height, width), dtype=np.uint8)
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
                return np.zeros((height, width), dtype=np.uint8)
            view = view[:size]
        arr = np.frombuffer(view, dtype=np.uint8, count=size)
        arr = arr.reshape((image.height(), bytes_per_line))[:, : image.width()]
        return arr.copy()
    return np.zeros((height, width), dtype=np.uint8)


def _numpy_to_qimage(mask: NDArray[Any], width: int, height: int) -> Any:
    try:
        from PySide6.QtGui import QImage  # noqa: PLC0415
    except ImportError:
        return None
    arr = np.ascontiguousarray(mask, dtype=np.uint8)
    if arr.shape != (height, width):
        arr = np.resize(arr, (height, width)).astype(np.uint8, copy=False)
    bytes_per_line = arr.strides[0]
    qimage = QImage(arr.data, width, height, bytes_per_line, QImage.Format.Format_Alpha8)
    return qimage.copy()


def _read_selection(canvas: Canvas) -> tuple[Any | None, SelectionRect | None]:
    mask = None
    bounds = None
    getter = getattr(canvas, "get_selection_mask", None)
    mask = getter() if callable(getter) else getattr(canvas, "_selection_mask", None)

    bounds_getter = getattr(canvas, "get_selection_bounds", None)
    if callable(bounds_getter):
        bounds = bounds_getter()
    else:
        bounds = getattr(canvas, "_selection_bounds", None)

    try:
        from PySide6.QtCore import QRect  # noqa: PLC0415
    except ImportError:
        QRect = None  # type: ignore[assignment,misc]  # optional PySide6 import fallback
    if QRect is not None and isinstance(bounds, QRect):
        bounds = (bounds.x(), bounds.y(), bounds.width(), bounds.height())
    if bounds is not None and not isinstance(bounds, tuple):
        bounds = None
    return mask, bounds


def _set_selection(canvas: Canvas, mask: Any | None, bounds: SelectionRect | None) -> None:
    setter = getattr(canvas, "set_selection_mask", None)
    if callable(setter):
        setter(mask, bounds)
        return
    if hasattr(canvas, "_selection_mask"):
        canvas._selection_mask = mask
    if hasattr(canvas, "_selection_bounds"):
        canvas._selection_bounds = bounds


def _clone_mask(mask: Any | None) -> Any | None:
    if mask is None:
        return None
    if isinstance(mask, np.ndarray):
        return mask.copy()
    try:
        from PySide6.QtGui import QImage  # noqa: PLC0415
    except ImportError:
        return None
    if isinstance(mask, QImage):
        return mask.copy()
    return None


def _convert_mask_for_canvas(
    canvas: Canvas, mask: NDArray[Any] | None, width: int, height: int
) -> Any | None:
    if mask is None:
        return None
    if _canvas_uses_qimage(canvas):
        return _numpy_to_qimage(mask, width, height)
    return mask.astype(np.uint8, copy=False)


def _canvas_uses_qimage(canvas: Canvas) -> bool:
    mask = getattr(canvas, "_selection_mask", None)
    try:
        from PySide6.QtGui import QImage  # noqa: PLC0415
    except ImportError:
        QImage = None  # type: ignore[assignment,misc]  # optional PySide6 import fallback
    if QImage is not None and isinstance(mask, QImage):
        return True
    if isinstance(mask, np.ndarray):
        return False
    if hasattr(canvas, "get_composited_pixmap") or hasattr(canvas, "composited_changed"):
        return True
    if hasattr(canvas, "config"):
        return False
    return False


def _is_noop(old_np: NDArray[Any], old_mask: Any | None, new_np: NDArray[Any] | None) -> bool:
    old_has = old_mask is not None and np.any(old_np)
    new_has = new_np is not None and np.any(new_np)
    if not old_has and not new_has:
        return True
    if old_has != new_has:
        return False
    if new_np is None:
        return False
    return np.array_equal(old_np, new_np)


def _record_legacy_history(
    canvas: Canvas,
    old_np: NDArray[Any],
    new_np: NDArray[Any] | None,
    width: int,
    height: int,
) -> None:
    recorder = getattr(canvas, "_record_selection_patch", None)
    if not callable(recorder):
        return
    before = old_np.astype(np.uint8, copy=False)
    if new_np is None:
        after = np.zeros((height, width), dtype=np.uint8)
    else:
        after = new_np.astype(np.uint8, copy=False)
    try:
        recorder(before, after)
    except Exception as exc:
        logger.warning("_record_selection_patch recorder failed: %s", exc, exc_info=True)
        return


def _mask_input_to_canvas_mask(  # noqa: PLR0911
    mask: Any,
    bounds: SelectionRect | None,
    width: int,
    height: int,
) -> NDArray[Any] | None:
    raw = _mask_to_numpy(mask, width, height)
    if raw.ndim != 2:
        return None
    raw = np.ascontiguousarray(raw, dtype=np.uint8)
    if raw.shape == (height, width):
        return raw.copy()

    original_bounds = _normalize_bounds(bounds)
    if original_bounds is None:
        return None
    clipped = _normalize_rect(original_bounds, width, height)
    if clipped is None:
        return None

    original_x, original_y, _original_w, _original_h = original_bounds
    dst_x, dst_y, dst_w, dst_h = clipped
    src_x = max(0, dst_x - int(original_x))
    src_y = max(0, dst_y - int(original_y))
    if src_x >= raw.shape[1] or src_y >= raw.shape[0]:
        return None

    paste_w = min(dst_w, raw.shape[1] - src_x)
    paste_h = min(dst_h, raw.shape[0] - src_y)
    if paste_w <= 0 or paste_h <= 0:
        return None

    canvas_mask = np.zeros((height, width), dtype=np.uint8)
    canvas_mask[dst_y : dst_y + paste_h, dst_x : dst_x + paste_w] = raw[
        src_y : src_y + paste_h,
        src_x : src_x + paste_w,
    ]
    return canvas_mask


def _selection_dirty_region(
    before_bounds: SelectionRect | None,
    after_bounds: SelectionRect | None,
    width: int,
    height: int,
) -> DirtyRegion:
    region: DirtyRegion | None = None
    for bounds in (before_bounds, after_bounds):
        rect = _normalize_rect(bounds, width, height)
        if rect is None:
            continue
        dirty = DirtyRegion(*rect)
        region = dirty if region is None else region.union(dirty)
    if region is not None:
        return region
    return DirtyRegion(0, 0, width, height)


class UpdateSelectionMaskCommand(Command):
    """Command that updates the selection mask with a rectangular operation."""

    def __init__(
        self,
        rect: SelectionRect | None,
        operation: SelectionOp | str,
        *,
        merge_key: str | None = None,
        source: str | None = None,
        full_bounds: SelectionRect | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.rect = rect
        self.operation = self._normalize_op(operation)
        self.source = source
        self.full_bounds = _normalize_bounds(full_bounds)
        self._before: SelectionSnapshot | None = None
        self._after: SelectionSnapshot | None = None
        self._before_full_bounds: SelectionRect | None = None
        self._before_full_mask: Any | None = None
        self._selection_id = SELECTION_MASK_LAYER_ID

    def apply(self, canvas: Canvas) -> bool:
        width, height = self._canvas_size(canvas)
        if width <= 0 or height <= 0:
            return False

        if self.rect is None and self.operation == SelectionOp.REPLACE:
            old_mask, old_bounds = self._read_selection(canvas)
            self._before = SelectionSnapshot(mask=self._clone_mask(old_mask), bounds=old_bounds)
            self._before_full_bounds = _get_selection_full_bounds(canvas)
            self._before_full_mask = _get_selection_full_mask(canvas)
            if old_bounds is None and old_mask is None:
                return False
            self._set_selection(canvas, None, None)
            _set_selection_full_bounds(canvas, None)
            _set_selection_full_mask(canvas, None)
            self._after = SelectionSnapshot(mask=None, bounds=None)
            self._dirty_region = DirtyRegion(0, 0, width, height)
            self._executed = True
            return True

        old_mask, old_bounds = self._read_selection(canvas)
        self._before = SelectionSnapshot(mask=self._clone_mask(old_mask), bounds=old_bounds)
        self._before_full_bounds = _get_selection_full_bounds(canvas)
        self._before_full_mask = _get_selection_full_mask(canvas)
        self._before_full_mask = _get_selection_full_mask(canvas)

        old_np = self._mask_to_numpy(old_mask, width, height)
        rect = self._normalize_rect(self.rect, width, height)
        rect_mask = self._rect_to_mask(rect, width, height) if rect else None
        new_np = self._compose_mask(old_np, rect_mask, self.operation, old_mask is not None)
        if new_np is not None and not np.any(new_np):
            new_np = None
        new_bounds = self._mask_bounds(new_np)

        if self._is_noop(old_np, old_mask, new_np):
            return False

        new_mask = self._convert_mask_for_canvas(canvas, new_np, width, height)
        self._set_selection(canvas, new_mask, new_bounds)
        full_bounds = self.full_bounds if self.full_bounds is not None else new_bounds
        _set_selection_full_bounds(canvas, full_bounds)
        if self.source != "preview":
            _set_selection_full_mask(canvas, None)
        self._after = SelectionSnapshot(mask=self._clone_mask(new_mask), bounds=new_bounds)

        if new_bounds is None:
            self._dirty_region = DirtyRegion(0, 0, width, height)
        else:
            self._dirty_region = DirtyRegion(*new_bounds)

        if self.source != "preview":
            self._record_legacy_history(canvas, old_np, new_np, width, height)

        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        if not self._before:
            return False
        width, height = self._canvas_size(canvas)
        self._set_selection(canvas, self._clone_mask(self._before.mask), self._before.bounds)
        _set_selection_full_bounds(canvas, self._before_full_bounds)
        _set_selection_full_mask(canvas, self._before_full_mask)
        if self._before.bounds is None:
            self._dirty_region = DirtyRegion(0, 0, width, height)
        else:
            self._dirty_region = DirtyRegion(*self._before.bounds)
        self._executed = False
        return True

    @staticmethod
    def _normalize_op(operation: SelectionOp | str) -> SelectionOp:
        return _normalize_op(operation)

    @staticmethod
    def _canvas_size(canvas: Canvas) -> tuple[int, int]:
        return _canvas_size(canvas)

    @staticmethod
    def _normalize_rect(
        rect: SelectionRect | None, width: int, height: int
    ) -> SelectionRect | None:
        return _normalize_rect(rect, width, height)

    @staticmethod
    def _rect_to_mask(rect: SelectionRect | None, width: int, height: int) -> NDArray[Any] | None:
        return _rect_to_mask(rect, width, height)

    @staticmethod
    def _compose_mask(
        old: NDArray[Any],
        rect_mask: NDArray[Any] | None,
        op: SelectionOp,
        had_mask: bool,
    ) -> NDArray[Any] | None:
        return _compose_mask(old, rect_mask, op, had_mask)

    @staticmethod
    def _mask_bounds(mask: NDArray[Any] | None) -> SelectionRect | None:
        return _mask_bounds(mask)

    @staticmethod
    def _mask_to_numpy(mask: Any | None, width: int, height: int) -> NDArray[Any]:
        return _mask_to_numpy(mask, width, height)

    @staticmethod
    def _numpy_to_qimage(mask: NDArray[Any], width: int, height: int) -> Any:
        return _numpy_to_qimage(mask, width, height)

    @staticmethod
    def _read_selection(canvas: Canvas) -> tuple[Any | None, SelectionRect | None]:
        return _read_selection(canvas)

    @staticmethod
    def _set_selection(canvas: Canvas, mask: Any | None, bounds: SelectionRect | None) -> None:
        _set_selection(canvas, mask, bounds)

    @staticmethod
    def _clone_mask(mask: Any | None) -> Any | None:
        return _clone_mask(mask)

    @staticmethod
    def _convert_mask_for_canvas(
        canvas: Canvas, mask: NDArray[Any] | None, width: int, height: int
    ) -> Any | None:
        return _convert_mask_for_canvas(canvas, mask, width, height)

    @staticmethod
    def _canvas_uses_qimage(canvas: Canvas) -> bool:
        return _canvas_uses_qimage(canvas)

    @staticmethod
    def _is_noop(old_np: NDArray[Any], old_mask: Any | None, new_np: NDArray[Any] | None) -> bool:
        return _is_noop(old_np, old_mask, new_np)

    @staticmethod
    def _record_legacy_history(
        canvas: Canvas,
        old_np: NDArray[Any],
        new_np: NDArray[Any] | None,
        width: int,
        height: int,
    ) -> None:
        _record_legacy_history(canvas, old_np, new_np, width, height)


class ApplySelectionMaskCommand(Command):
    """Command that composes an arbitrary alpha mask into the selection mask."""

    def __init__(
        self,
        mask: Any | None,
        operation: SelectionOp | str,
        *,
        bounds: SelectionRect | None = None,
        merge_key: str | None = None,
        source: str | None = None,
        full_bounds: SelectionRect | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.mask = self._clone_mask(mask) if mask is not None else None
        if self.mask is None and mask is not None:
            self.mask = mask
        self.operation = self._normalize_op(operation)
        self.bounds = _normalize_bounds(bounds)
        self.source = source
        self.full_bounds = _normalize_bounds(full_bounds)
        self._before: SelectionSnapshot | None = None
        self._after: SelectionSnapshot | None = None
        self._before_full_bounds: SelectionRect | None = None
        self._before_full_mask: Any | None = None
        self._selection_id = SELECTION_MASK_LAYER_ID

    def apply(self, canvas: Canvas) -> bool:
        width, height = self._canvas_size(canvas)
        if width <= 0 or height <= 0:
            return False

        old_mask, old_bounds = self._read_selection(canvas)
        self._before = SelectionSnapshot(mask=self._clone_mask(old_mask), bounds=old_bounds)
        self._before_full_bounds = _get_selection_full_bounds(canvas)
        self._before_full_mask = _get_selection_full_mask(canvas)

        old_np = self._mask_to_numpy(old_mask, width, height)
        incoming_np: NDArray[Any] | None = None
        if self.mask is not None:
            incoming_np = _mask_input_to_canvas_mask(self.mask, self.bounds, width, height)
            if incoming_np is None or not np.any(incoming_np):
                return False

        new_np = self._compose_mask(
            old_np,
            incoming_np,
            self.operation,
            old_mask is not None,
        )
        if new_np is not None and not np.any(new_np):
            new_np = None
        new_bounds = self._mask_bounds(new_np)

        if self._is_noop(old_np, old_mask, new_np):
            return False

        new_mask = self._convert_mask_for_canvas(canvas, new_np, width, height)
        self._set_selection(canvas, new_mask, new_bounds)
        _set_selection_full_bounds(canvas, self._full_bounds_for(new_bounds))
        if self.source != "preview":
            _set_selection_full_mask(canvas, None)
        self._after = SelectionSnapshot(mask=self._clone_mask(new_mask), bounds=new_bounds)
        self._dirty_region = _selection_dirty_region(old_bounds, new_bounds, width, height)

        if self.source != "preview":
            self._record_legacy_history(canvas, old_np, new_np, width, height)

        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        if not self._before:
            return False
        width, height = self._canvas_size(canvas)
        self._set_selection(canvas, self._clone_mask(self._before.mask), self._before.bounds)
        _set_selection_full_bounds(canvas, self._before_full_bounds)
        _set_selection_full_mask(canvas, self._before_full_mask)
        self._dirty_region = _selection_dirty_region(
            self._after_bounds, self._before.bounds, width, height
        )
        self._executed = False
        return True

    @property
    def _after_bounds(self) -> SelectionRect | None:
        return self._after.bounds if self._after else None

    def _full_bounds_for(self, new_bounds: SelectionRect | None) -> SelectionRect | None:
        if new_bounds is None:
            return None
        if self.full_bounds is not None:
            return self.full_bounds
        if self.operation == SelectionOp.REPLACE and self.bounds is not None:
            return self.bounds
        return new_bounds

    @staticmethod
    def _normalize_op(operation: SelectionOp | str) -> SelectionOp:
        return _normalize_op(operation)

    @staticmethod
    def _canvas_size(canvas: Canvas) -> tuple[int, int]:
        return _canvas_size(canvas)

    @staticmethod
    def _compose_mask(
        old: NDArray[Any],
        rect_mask: NDArray[Any] | None,
        op: SelectionOp,
        had_mask: bool,
    ) -> NDArray[Any] | None:
        return _compose_mask(old, rect_mask, op, had_mask)

    @staticmethod
    def _mask_bounds(mask: NDArray[Any] | None) -> SelectionRect | None:
        return _mask_bounds(mask)

    @staticmethod
    def _mask_to_numpy(mask: Any | None, width: int, height: int) -> NDArray[Any]:
        return _mask_to_numpy(mask, width, height)

    @staticmethod
    def _read_selection(canvas: Canvas) -> tuple[Any | None, SelectionRect | None]:
        return _read_selection(canvas)

    @staticmethod
    def _set_selection(canvas: Canvas, mask: Any | None, bounds: SelectionRect | None) -> None:
        _set_selection(canvas, mask, bounds)

    @staticmethod
    def _clone_mask(mask: Any | None) -> Any | None:
        return _clone_mask(mask)

    @staticmethod
    def _convert_mask_for_canvas(
        canvas: Canvas, mask: NDArray[Any] | None, width: int, height: int
    ) -> Any | None:
        return _convert_mask_for_canvas(canvas, mask, width, height)

    @staticmethod
    def _is_noop(old_np: NDArray[Any], old_mask: Any | None, new_np: NDArray[Any] | None) -> bool:
        return _is_noop(old_np, old_mask, new_np)

    @staticmethod
    def _record_legacy_history(
        canvas: Canvas,
        old_np: NDArray[Any],
        new_np: NDArray[Any] | None,
        width: int,
        height: int,
    ) -> None:
        _record_legacy_history(canvas, old_np, new_np, width, height)


_MAX_SELECTION_RADIUS = 128


def _clamp_radius(radius: int) -> int:
    return max(0, min(int(radius), _MAX_SELECTION_RADIUS))


def _clip_bounds_to_canvas(canvas: Canvas, bounds: SelectionRect) -> SelectionRect | None:
    width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
    x, y, w, h = bounds
    x1 = max(0, int(x))
    y1 = max(0, int(y))
    x2 = min(width, int(x + w))
    y2 = min(height, int(y + h))
    if x1 >= x2 or y1 >= y2:
        return None
    return (x1, y1, x2 - x1, y2 - y1)


def _normalize_bounds(bounds: Any | None) -> SelectionRect | None:
    if bounds is None:
        return None
    if hasattr(bounds, "x") and hasattr(bounds, "y"):
        return (int(bounds.x()), int(bounds.y()), int(bounds.width()), int(bounds.height()))
    if isinstance(bounds, (tuple, list)) and len(bounds) == 4:
        return (int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3]))
    return None


def _get_selection_full_bounds(canvas: Canvas) -> SelectionRect | None:
    return _normalize_bounds(getattr(cast(Any, canvas), "_selection_bounds_full", None))


def _set_selection_full_bounds(canvas: Canvas, bounds: SelectionRect | None) -> None:
    try:
        cast(Any, canvas)._selection_bounds_full = bounds
    except Exception as exc:
        logger.warning(
            "_set_selection_full_bounds failed to write canvas._selection_bounds_full: %s",
            exc,
            exc_info=True,
        )
        return


def _get_selection_full_mask(canvas: Canvas) -> Any | None:
    return getattr(canvas, "_selection_mask_full", None)


def _set_selection_full_mask(canvas: Canvas, mask: Any | None) -> None:
    try:
        canvas._selection_mask_full = mask
    except Exception as exc:
        logger.warning(
            "_set_selection_full_mask failed to write canvas._selection_mask_full: %s",
            exc,
            exc_info=True,
        )
        return


class ModifySelectionMaskCommand(Command):
    """Command to modify selection mask with invert/expand/contract/feather."""

    def __init__(
        self,
        op: str,
        radius: int = 0,
        *,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.op = self._normalize_op(op)
        self.radius = int(radius)
        self._before: SelectionSnapshot | None = None
        self._after: SelectionSnapshot | None = None
        self._before_full_bounds: SelectionRect | None = None
        self._before_full_mask: Any | None = None

    def apply(self, canvas: Canvas) -> bool:
        width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
        if width <= 0 or height <= 0:
            return False

        old_mask, old_bounds = UpdateSelectionMaskCommand._read_selection(canvas)
        self._before = SelectionSnapshot(
            mask=UpdateSelectionMaskCommand._clone_mask(old_mask),
            bounds=old_bounds,
        )
        self._before_full_bounds = _get_selection_full_bounds(canvas)
        self._before_full_mask = _get_selection_full_mask(canvas)

        old_np = UpdateSelectionMaskCommand._mask_to_numpy(old_mask, width, height)
        radius = _clamp_radius(self.radius)

        if self.op != "invert" and radius <= 0:
            return False
        if self.op != "invert" and not np.any(old_np):
            return False

        new_np: NDArray[Any] | None
        if self.op == "invert":
            new_np = (255 - old_np).astype(np.uint8, copy=False)
        elif self.op == "expand":
            new_np = self._dilate(old_np, radius)
        elif self.op == "contract":
            new_np = self._erode(old_np, radius)
        else:
            new_np = self._feather(old_np, radius)

        if new_np is not None and not np.any(new_np):
            new_np = None

        new_bounds = UpdateSelectionMaskCommand._mask_bounds(new_np)
        if UpdateSelectionMaskCommand._is_noop(old_np, old_mask, new_np):
            return False

        new_mask = UpdateSelectionMaskCommand._convert_mask_for_canvas(
            canvas, new_np, width, height
        )
        UpdateSelectionMaskCommand._set_selection(canvas, new_mask, new_bounds)
        _set_selection_full_bounds(canvas, new_bounds)
        _set_selection_full_mask(canvas, None)
        self._after = SelectionSnapshot(
            mask=UpdateSelectionMaskCommand._clone_mask(new_mask),
            bounds=new_bounds,
        )

        if new_bounds is None:
            self._dirty_region = DirtyRegion(0, 0, width, height)
        else:
            self._dirty_region = DirtyRegion(*new_bounds)

        UpdateSelectionMaskCommand._record_legacy_history(canvas, old_np, new_np, width, height)

        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        if not self._before:
            return False
        width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
        UpdateSelectionMaskCommand._set_selection(
            canvas,
            UpdateSelectionMaskCommand._clone_mask(self._before.mask),
            self._before.bounds,
        )
        _set_selection_full_bounds(canvas, self._before_full_bounds)
        _set_selection_full_mask(canvas, self._before_full_mask)
        if self._before.bounds is None:
            self._dirty_region = DirtyRegion(0, 0, width, height)
        else:
            self._dirty_region = DirtyRegion(*self._before.bounds)
        self._executed = False
        return True

    @staticmethod
    def _normalize_op(op: str) -> str:
        value = str(op).strip().lower()
        if value in {"invert", "expand", "contract", "feather"}:
            return value
        return "invert"

    @staticmethod
    def _dilate(mask: NDArray[Any], radius: int) -> NDArray[Any]:
        if radius <= 0:
            return mask.copy()
        height, width = mask.shape
        padded = np.pad(mask, radius, mode="constant", constant_values=0)
        windows: list[NDArray[Any]] = []
        for dy in range(2 * radius + 1):
            for dx in range(2 * radius + 1):
                windows.append(padded[dy : dy + height, dx : dx + width])
        return cast("NDArray[Any]", np.maximum.reduce(windows).astype(np.uint8, copy=False))

    @staticmethod
    def _erode(mask: NDArray[Any], radius: int) -> NDArray[Any]:
        if radius <= 0:
            return mask.copy()
        height, width = mask.shape
        padded = np.pad(mask, radius, mode="constant", constant_values=0)
        windows: list[NDArray[Any]] = []
        for dy in range(2 * radius + 1):
            for dx in range(2 * radius + 1):
                windows.append(padded[dy : dy + height, dx : dx + width])
        return cast("NDArray[Any]", np.minimum.reduce(windows).astype(np.uint8, copy=False))

    @staticmethod
    def _feather(mask: NDArray[Any], radius: int) -> NDArray[Any]:
        if radius <= 0:
            return mask.copy()
        radius = _clamp_radius(radius)
        sigma = max(radius / 3.0, 0.1)
        axis = np.arange(-radius, radius + 1, dtype=np.float32)
        kernel = np.exp(-(axis**2) / (2.0 * sigma**2))
        kernel = kernel / np.sum(kernel)

        def _convolve_axis(values: NDArray[Any], axis_index: int) -> NDArray[Any]:
            pad = radius
            pad_width = [(0, 0)] * values.ndim
            pad_width[axis_index] = (pad, pad)
            padded = np.pad(values, pad_width, mode="edge")
            return np.apply_along_axis(
                lambda v: np.convolve(v, kernel, mode="valid"),
                axis_index,
                padded,
            )

        data = mask.astype(np.float32, copy=False)
        blurred = _convolve_axis(_convolve_axis(data, 0), 1)
        return np.clip(blurred, 0.0, 255.0).astype(np.uint8)
