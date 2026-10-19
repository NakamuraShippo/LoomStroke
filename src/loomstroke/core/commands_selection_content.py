"""Selection content commands and payload transform helpers."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from .command_image_utils import (
    _affine_bounds,
    _composite_nonpremultiplied,
    _composite_premultiplied,
    _ensure_qimage_premultiplied,
    _is_qimage_layer,
    _numpy_bgra_to_rgba,
    _numpy_rgba_to_bgra,
    _place_mask_on_canvas,
    _premultiply_rgba,
    _qimage_to_numpy_copy,
    _qimage_to_numpy_view,
    _union_bounds,
)
from .command_layer_access import (
    _canvas_dpi,
    _ensure_layer_bounds,
    _layer_allows_edit,
    _layer_offset,
    _resolve_target_layer,
    _supports_layer_expansion,
)
from .command_types import Command, DirtyRegion, SelectionRect, SelectionSnapshot
from .commands_selection_mask import (
    UpdateSelectionMaskCommand,
    _clip_bounds_to_canvas,
    _get_selection_full_bounds,
    _get_selection_full_mask,
    _set_selection_full_bounds,
    _set_selection_full_mask,
)
from .geometry import Affine2D
from .selection_clipboard import SelectionPayload

logger = logging.getLogger("loomstroke.core.commands")

if TYPE_CHECKING:
    from .canvas import Canvas


def _selection_payload_mask_numpy(payload: SelectionPayload) -> NDArray[Any] | None:
    mask = payload.mask
    if isinstance(mask, np.ndarray):
        return mask.astype(np.uint8, copy=False)
    try:
        from PySide6.QtGui import QImage  # noqa: PLC0415
    except ImportError:
        return None
    if isinstance(mask, QImage):
        return UpdateSelectionMaskCommand._mask_to_numpy(mask, mask.width(), mask.height())
    return None


def _selection_payload_image_numpy(
    payload: SelectionPayload, *, premultiplied: bool
) -> NDArray[Any] | None:
    image = payload.image
    if isinstance(image, np.ndarray):
        data = image.astype(np.uint8, copy=False)
        if premultiplied:
            return _numpy_rgba_to_bgra(_premultiply_rgba(data))
        return data
    try:
        from PySide6.QtGui import QImage  # noqa: PLC0415
    except ImportError:
        return None
    if isinstance(image, QImage):
        qimage = image
        if premultiplied and qimage.format() != QImage.Format.Format_ARGB32_Premultiplied:
            qimage = qimage.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        elif not premultiplied and qimage.format() != QImage.Format.Format_ARGB32:
            qimage = qimage.convertToFormat(QImage.Format.Format_ARGB32)
        qimage_data = _qimage_to_numpy_copy(qimage)
        if qimage_data is None:
            return None
        return qimage_data if premultiplied else _numpy_bgra_to_rgba(qimage_data)
    return None


def _transform_payload_raster(  # noqa: PLR0915
    payload: SelectionPayload,
    transform: Affine2D,
    *,
    premultiplied: bool,
    mask_mode: str,
    canvas_size: tuple[int, int] | None = None,
) -> tuple[NDArray[Any], NDArray[Any], SelectionRect] | None:
    mask = _selection_payload_mask_numpy(payload)
    image = _selection_payload_image_numpy(payload, premultiplied=premultiplied)
    if mask is None or image is None:
        return None
    if mask_mode == "binary":
        mask = np.where(mask > 127, 255, 0).astype(np.uint8, copy=False)
    height, width = mask.shape
    bounds = _affine_bounds(transform, width, height)
    if bounds is None:
        return None
    inv = transform.inverse()
    if inv is None:
        return None
    dest_x, dest_y, dest_w, dest_h = bounds
    dest_image = np.zeros((dest_h, dest_w, 4), dtype=np.uint8)
    dest_mask = np.zeros((dest_h, dest_w), dtype=np.uint8)

    for j in range(dest_h):
        cy = dest_y + j + 0.5
        for i in range(dest_w):
            cx = dest_x + i + 0.5
            u, v = inv.apply(cx, cy)
            u -= 0.5
            v -= 0.5
            if u < 0.0 or v < 0.0 or u >= width or v >= height:
                continue
            x0 = math.floor(u)
            y0 = math.floor(v)
            x1 = min(x0 + 1, width - 1)
            y1 = min(y0 + 1, height - 1)
            dx = u - x0
            dy = v - y0
            wa = (1.0 - dx) * (1.0 - dy)
            wb = dx * (1.0 - dy)
            wc = (1.0 - dx) * dy
            wd = dx * dy

            if mask_mode == "soft":
                mask_val = (
                    mask[y0, x0] * wa + mask[y0, x1] * wb + mask[y1, x0] * wc + mask[y1, x1] * wd
                )
            else:
                ix = round(u)
                iy = round(v)
                ix = max(0, min(width - 1, ix))
                iy = max(0, min(height - 1, iy))
                mask_val = 255.0 if mask[iy, ix] > 127 else 0.0

            if mask_val <= 0.0:
                continue
            dest_mask[j, i] = int(max(0.0, min(255.0, mask_val)))

            pixel = (
                image[y0, x0] * wa + image[y0, x1] * wb + image[y1, x0] * wc + image[y1, x1] * wd
            )
            dest_image[j, i] = np.clip(pixel, 0.0, 255.0).astype(np.uint8)

    if canvas_size is None:
        return dest_image, dest_mask, bounds

    canvas_w, canvas_h = canvas_size
    clip_x1 = max(0, dest_x)
    clip_y1 = max(0, dest_y)
    clip_x2 = min(canvas_w, dest_x + dest_w)
    clip_y2 = min(canvas_h, dest_y + dest_h)
    if clip_x2 <= clip_x1 or clip_y2 <= clip_y1:
        return None
    src_x1 = clip_x1 - dest_x
    src_y1 = clip_y1 - dest_y
    src_x2 = src_x1 + (clip_x2 - clip_x1)
    src_y2 = src_y1 + (clip_y2 - clip_y1)
    dest_image_clipped: NDArray[Any] = dest_image[src_y1:src_y2, src_x1:src_x2]
    dest_mask_clipped: NDArray[Any] = dest_mask[src_y1:src_y2, src_x1:src_x2]
    clipped_bounds = (clip_x1, clip_y1, clip_x2 - clip_x1, clip_y2 - clip_y1)
    return dest_image_clipped, dest_mask_clipped, clipped_bounds


def _transform_payload_qimage(  # noqa: PLR0911, PLR0912, PLR0915
    payload: SelectionPayload,
    transform: Affine2D,
    *,
    premultiplied: bool,
    mask_mode: str,
    canvas_size: tuple[int, int] | None = None,
) -> tuple[Any, Any, SelectionRect] | None:
    try:
        from PySide6.QtCore import Qt  # noqa: PLC0415
        from PySide6.QtGui import QImage, QPainter, QTransform  # noqa: PLC0415
    except ImportError:
        return None
    if not isinstance(payload.image, QImage) or not isinstance(payload.mask, QImage):
        return None

    image_source = payload.image
    if premultiplied:
        if image_source.format() != QImage.Format.Format_ARGB32_Premultiplied:
            image_source = image_source.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        image_format = QImage.Format.Format_ARGB32_Premultiplied
    else:
        if image_source.format() != QImage.Format.Format_ARGB32:
            image_source = image_source.convertToFormat(QImage.Format.Format_ARGB32)
        image_format = QImage.Format.Format_ARGB32

    mask_source = payload.mask
    if mask_source.format() != QImage.Format.Format_Alpha8:
        mask_source = mask_source.convertToFormat(QImage.Format.Format_Alpha8)
    if mask_mode == "binary":
        mask_np = UpdateSelectionMaskCommand._mask_to_numpy(
            mask_source, mask_source.width(), mask_source.height()
        )
        mask_np = np.where(mask_np > 127, 255, 0).astype(np.uint8, copy=False)
        mask_qimage = UpdateSelectionMaskCommand._numpy_to_qimage(
            mask_np, mask_source.width(), mask_source.height()
        )
        if mask_qimage is None:
            return None
        mask_source = mask_qimage

    bounds = _affine_bounds(transform, mask_source.width(), mask_source.height())
    if bounds is None:
        return None
    dest_x, dest_y, dest_w, dest_h = bounds
    if dest_w <= 0 or dest_h <= 0:
        return None

    transform_qt = QTransform(
        transform.a,
        transform.c,
        transform.b,
        transform.d,
        transform.tx - dest_x,
        transform.ty - dest_y,
    )

    dest_image = QImage(dest_w, dest_h, image_format)
    dest_image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(dest_image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setTransform(transform_qt)
    painter.drawImage(0, 0, image_source)
    painter.end()

    dest_mask = QImage(dest_w, dest_h, QImage.Format.Format_Alpha8)
    dest_mask.fill(0)
    painter = QPainter(dest_mask)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    painter.setRenderHint(
        QPainter.RenderHint.SmoothPixmapTransform,
        mask_mode == "soft",
    )
    painter.setTransform(transform_qt)
    painter.drawImage(0, 0, mask_source)
    painter.end()

    if canvas_size is None:
        return dest_image, dest_mask, bounds

    canvas_w, canvas_h = canvas_size
    clip_x1 = max(0, dest_x)
    clip_y1 = max(0, dest_y)
    clip_x2 = min(canvas_w, dest_x + dest_w)
    clip_y2 = min(canvas_h, dest_y + dest_h)
    if clip_x2 <= clip_x1 or clip_y2 <= clip_y1:
        return None
    src_x1 = clip_x1 - dest_x
    src_y1 = clip_y1 - dest_y
    clip_w = clip_x2 - clip_x1
    clip_h = clip_y2 - clip_y1
    dest_image = dest_image.copy(int(src_x1), int(src_y1), int(clip_w), int(clip_h))
    dest_mask = dest_mask.copy(int(src_x1), int(src_y1), int(clip_w), int(clip_h))
    clipped_bounds = (clip_x1, clip_y1, clip_w, clip_h)
    return dest_image, dest_mask, clipped_bounds


class CutSelectionCommand(Command):
    """Cut the active selection from a layer and capture payload."""

    def __init__(self, layer_id: str | None, *, merge_key: str | None = None) -> None:
        super().__init__(merge_key=merge_key)
        self.layer_id = layer_id
        self.payload: SelectionPayload | None = None
        self._before_selection: SelectionSnapshot | None = None
        self._after_selection: SelectionSnapshot | None = None
        self._before_full_bounds: SelectionRect | None = None
        self._before_full_mask: Any | None = None
        self._before_layer: Any | None = None
        self._after_layer: Any | None = None
        self._bounds: SelectionRect | None = None
        self._layer_ref: Any | None = None

    def apply(self, canvas: Canvas) -> bool:  # noqa: PLR0911, PLR0912, PLR0915
        from .native_cmyk.selection_commands import apply  # noqa: PLC0415

        native = apply(self, canvas)
        if native is not None:
            return native
        width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
        if width <= 0 or height <= 0:
            return False
        old_mask, old_bounds = UpdateSelectionMaskCommand._read_selection(canvas)
        self._before_full_bounds = _get_selection_full_bounds(canvas)
        self._before_full_mask = _get_selection_full_mask(canvas)
        old_np = UpdateSelectionMaskCommand._mask_to_numpy(old_mask, width, height)

        layer = _resolve_target_layer(canvas, self.layer_id)
        if layer is None or not _layer_allows_edit(layer):
            return False

        bounds = old_bounds or UpdateSelectionMaskCommand._mask_bounds(old_np)
        full_bounds = self._before_full_bounds
        full_mask_raw = self._before_full_mask
        full_mask_np: NDArray[Any] | None = None
        use_full = False
        if (
            full_mask_raw is not None
            and full_bounds is not None
            and _supports_layer_expansion(canvas, layer)
        ):
            if isinstance(full_mask_raw, np.ndarray):
                full_mask_np = full_mask_raw.astype(np.uint8, copy=False)
            else:
                try:
                    from PySide6.QtGui import QImage  # noqa: PLC0415
                except ImportError:
                    QImage = None  # type: ignore[assignment,misc]  # optional PySide6 import fallback
                if QImage is not None and isinstance(full_mask_raw, QImage):
                    full_mask_np = UpdateSelectionMaskCommand._mask_to_numpy(
                        full_mask_raw, full_mask_raw.width(), full_mask_raw.height()
                    )
            if full_mask_np is not None and (
                full_mask_np.shape[0] == int(full_bounds[3])
                and full_mask_np.shape[1] == int(full_bounds[2])
                and np.any(full_mask_np)
            ):
                if _is_qimage_layer(layer):
                    image_ref = getattr(layer, "image", None)
                    if image_ref is not None and not image_ref.isNull():
                        offset_x, offset_y = _layer_offset(layer)
                        fx, fy, fw, fh = full_bounds
                        if (
                            (fx - offset_x) >= 0
                            and (fy - offset_y) >= 0
                            and (fx - offset_x + fw) <= image_ref.width()
                            and (fy - offset_y + fh) <= image_ref.height()
                        ):
                            use_full = True
                else:
                    use_full = True

        if use_full and full_mask_np is not None and full_bounds is not None:
            bounds = full_bounds
            x, y, w, h = bounds
            mask_patch = full_mask_np
        else:
            if bounds is None:
                return False
            x, y, w, h = bounds
            mask_patch = old_np[y : y + h, x : x + w]
        self._before_selection = SelectionSnapshot(
            mask=UpdateSelectionMaskCommand._clone_mask(old_mask),
            bounds=old_bounds,
        )
        if mask_patch.size == 0 or not np.any(mask_patch):
            return False

        self._layer_ref = layer
        if _is_qimage_layer(layer):
            image = _ensure_qimage_premultiplied(layer)
            if image is None:
                return False
            try:
                from PySide6.QtCore import QRect  # noqa: PLC0415
            except ImportError:
                return False
            offset_x, offset_y = _layer_offset(layer)
            rect = QRect(int(x - offset_x), int(y - offset_y), int(w), int(h))
            self._before_layer = image.copy(rect)
            payload_image = image.copy(rect)
            payload_mask = UpdateSelectionMaskCommand._numpy_to_qimage(mask_patch, w, h)
            if payload_mask is None:
                return False
            self.payload = SelectionPayload(
                image=payload_image,
                mask=payload_mask,
                bounds=bounds,
                source_layer_id=getattr(layer, "id", None),
                dpi=_canvas_dpi(canvas),
            )
            view = _qimage_to_numpy_view(image)
            if view is None:
                return False
            region = view[rect.y() : rect.y() + h, rect.x() : rect.x() + w]
            factor = (255.0 - mask_patch.astype(np.float32)) / 255.0
            region[:] = np.clip(
                region.astype(np.float32) * factor[..., None],
                0.0,
                255.0,
            ).astype(np.uint8)
            self._after_layer = image.copy(rect)
        else:
            buffer = getattr(layer, "buffer", None)
            if not isinstance(buffer, np.ndarray) or buffer.ndim != 3:
                return False
            region = buffer[y : y + h, x : x + w]
            self._before_layer = region.copy()
            payload_image = np.clip(region, 0.0, 255.0).astype(np.uint8)
            self.payload = SelectionPayload(
                image=payload_image,
                mask=mask_patch.copy(),
                bounds=bounds,
                source_layer_id=getattr(layer, "id", None),
                dpi=_canvas_dpi(canvas),
            )
            factor = (255.0 - mask_patch.astype(np.float32)) / 255.0
            region[:] = region * factor[..., None]
            self._after_layer = region.copy()
            if hasattr(layer, "mark_dirty"):
                layer.mark_dirty(x, y, w, h)

        UpdateSelectionMaskCommand._set_selection(
            canvas,
            UpdateSelectionMaskCommand._convert_mask_for_canvas(canvas, old_np, width, height),
            bounds,
        )
        if self._before_full_bounds is not None:
            _set_selection_full_bounds(canvas, self._before_full_bounds)
        else:
            _set_selection_full_bounds(canvas, bounds)
        if use_full and full_mask_np is not None:
            _set_selection_full_mask(canvas, full_mask_np)
        else:
            _set_selection_full_mask(canvas, self._before_full_mask)
        self._after_selection = SelectionSnapshot(
            mask=UpdateSelectionMaskCommand._clone_mask(old_mask),
            bounds=bounds,
        )
        self._dirty_region = DirtyRegion(x, y, w, h)
        self._bounds = bounds
        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        from .native_cmyk.selection_commands import revert  # noqa: PLC0415

        native = revert(self, canvas)
        if (
            native is not None
            or not self._before_selection
            or self._bounds is None
            or self._layer_ref is None
        ):
            return bool(native)
        x, y, w, h = self._bounds
        layer = self._layer_ref
        if _is_qimage_layer(layer):
            image = _ensure_qimage_premultiplied(layer)
            if image is None:
                return False
            try:
                from PySide6.QtGui import QPainter  # noqa: PLC0415
            except ImportError:
                return False
            if self._before_layer is None:
                return False
            offset_x, offset_y = _layer_offset(layer)
            painter = QPainter(image)
            painter.drawImage(int(x - offset_x), int(y - offset_y), self._before_layer)
            painter.end()
        else:
            buffer = getattr(layer, "buffer", None)
            if not isinstance(buffer, np.ndarray) or self._before_layer is None:
                return False
            buffer[y : y + h, x : x + w] = self._before_layer
            if hasattr(layer, "mark_dirty"):
                layer.mark_dirty(x, y, w, h)

        UpdateSelectionMaskCommand._set_selection(
            canvas,
            UpdateSelectionMaskCommand._clone_mask(self._before_selection.mask),
            self._before_selection.bounds,
        )
        _set_selection_full_bounds(canvas, self._before_full_bounds)
        _set_selection_full_mask(canvas, self._before_full_mask)
        self._dirty_region = DirtyRegion(x, y, w, h)
        self._executed = False
        return True


class PasteSelectionCommand(Command):
    """Paste a selection payload onto a target layer."""

    def __init__(
        self,
        payload: SelectionPayload,
        position: tuple[int, int] | None = None,
        *,
        layer_id: str | None = None,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.payload = payload
        self.position = position
        self.layer_id = layer_id
        self._before_selection: SelectionSnapshot | None = None
        self._after_selection: SelectionSnapshot | None = None
        self._before_full_bounds: SelectionRect | None = None
        self._before_full_mask: Any | None = None
        self._before_layer: Any | None = None
        self._after_layer: Any | None = None
        self._bounds: SelectionRect | None = None
        self._layer_ref: Any | None = None

    def apply(self, canvas: Canvas) -> bool:  # noqa: PLR0911, PLR0912, PLR0915
        from .native_cmyk.selection_commands import apply  # noqa: PLC0415

        native = apply(self, canvas)
        if native is not None:
            return native
        width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
        if width <= 0 or height <= 0:
            return False
        if self.payload is None:
            return False
        self._before_full_bounds = _get_selection_full_bounds(canvas)
        self._before_full_mask = _get_selection_full_mask(canvas)

        layer = _resolve_target_layer(canvas, self.layer_id)
        if layer is None or not _layer_allows_edit(layer):
            return False

        dest_x, dest_y = self.position or (self.payload.bounds[0], self.payload.bounds[1])
        self._before_full_bounds = _get_selection_full_bounds(canvas)
        payload_mask = self._payload_mask_numpy()
        if payload_mask is None:
            return False
        bounds_full = (
            int(dest_x),
            int(dest_y),
            int(payload_mask.shape[1]),
            int(payload_mask.shape[0]),
        )
        supports_expand = _supports_layer_expansion(canvas, layer)
        if supports_expand:
            _ensure_layer_bounds(canvas, layer, bounds_full)
            offset_x, offset_y = _layer_offset(layer)
            x, y, w, h = bounds_full
            self._bounds = bounds_full
            self._layer_ref = layer

            old_mask, old_bounds = UpdateSelectionMaskCommand._read_selection(canvas)
            self._before_selection = SelectionSnapshot(
                mask=UpdateSelectionMaskCommand._clone_mask(old_mask),
                bounds=old_bounds,
            )

            image = _ensure_qimage_premultiplied(layer)
            if image is None:
                return False
            try:
                from PySide6.QtCore import QRect  # noqa: PLC0415
            except ImportError:
                return False
            rect = QRect(int(x - offset_x), int(y - offset_y), int(w), int(h))
            self._before_layer = image.copy(rect)
            payload_image = self._payload_image_numpy(premultiplied=True)
            if payload_image is None:
                return False
            view = _qimage_to_numpy_view(image)
            if view is None:
                return False
            region = view[rect.y() : rect.y() + h, rect.x() : rect.x() + w]
            _composite_premultiplied(region, payload_image, payload_mask)
            self._after_layer = image.copy(rect)

            mask_full, bounds, _ = _place_mask_on_canvas(
                payload_mask,
                position=(dest_x, dest_y),
                width=width,
                height=height,
            )
            if bounds is None:
                UpdateSelectionMaskCommand._set_selection(canvas, None, None)
                _set_selection_full_bounds(canvas, None)
                _set_selection_full_mask(canvas, None)
                self._after_selection = SelectionSnapshot(mask=None, bounds=None)
            else:
                canvas_mask = UpdateSelectionMaskCommand._convert_mask_for_canvas(
                    canvas, mask_full, width, height
                )
                UpdateSelectionMaskCommand._set_selection(canvas, canvas_mask, bounds)
                _set_selection_full_bounds(canvas, bounds_full)
                _set_selection_full_mask(canvas, payload_mask)
                self._after_selection = SelectionSnapshot(
                    mask=UpdateSelectionMaskCommand._clone_mask(canvas_mask),
                    bounds=bounds,
                )
            clipped = _clip_bounds_to_canvas(canvas, bounds_full)
            if clipped is not None:
                self._dirty_region = DirtyRegion(*clipped)
            self._executed = True
            return True

        mask_full, bounds, src_bounds = _place_mask_on_canvas(
            payload_mask,
            position=(dest_x, dest_y),
            width=width,
            height=height,
        )
        if bounds is None or src_bounds is None:
            return False

        x, y, w, h = bounds
        src_x, src_y, src_w, src_h = src_bounds
        self._bounds = bounds
        self._layer_ref = layer

        old_mask, old_bounds = UpdateSelectionMaskCommand._read_selection(canvas)
        self._before_selection = SelectionSnapshot(
            mask=UpdateSelectionMaskCommand._clone_mask(old_mask),
            bounds=old_bounds,
        )

        if _is_qimage_layer(layer):
            image = _ensure_qimage_premultiplied(layer)
            if image is None:
                return False
            try:
                from PySide6.QtCore import QRect  # noqa: PLC0415
            except ImportError:
                return False
            rect = QRect(int(x), int(y), int(w), int(h))
            self._before_layer = image.copy(rect)
            payload_image = self._payload_image_numpy(premultiplied=True)
            if payload_image is None:
                return False
            view = _qimage_to_numpy_view(image)
            if view is None:
                return False
            region = view[y : y + h, x : x + w]
            _composite_premultiplied(
                region,
                payload_image[src_y : src_y + src_h, src_x : src_x + src_w],
                payload_mask[src_y : src_y + src_h, src_x : src_x + src_w],
            )
            self._after_layer = image.copy(rect)
        else:
            buffer = getattr(layer, "buffer", None)
            if not isinstance(buffer, np.ndarray) or buffer.ndim != 3:
                return False
            region = buffer[y : y + h, x : x + w]
            self._before_layer = region.copy()
            payload_image = self._payload_image_numpy(premultiplied=False)
            if payload_image is None:
                return False
            _composite_nonpremultiplied(
                region,
                payload_image[src_y : src_y + src_h, src_x : src_x + src_w],
                payload_mask[src_y : src_y + src_h, src_x : src_x + src_w],
            )
            self._after_layer = region.copy()
            if hasattr(layer, "mark_dirty"):
                layer.mark_dirty(x, y, w, h)

        UpdateSelectionMaskCommand._set_selection(
            canvas,
            UpdateSelectionMaskCommand._convert_mask_for_canvas(canvas, mask_full, width, height),
            bounds,
        )
        _set_selection_full_bounds(canvas, bounds_full)
        _set_selection_full_mask(canvas, None)
        self._after_selection = SelectionSnapshot(
            mask=UpdateSelectionMaskCommand._clone_mask(
                UpdateSelectionMaskCommand._convert_mask_for_canvas(
                    canvas, mask_full, width, height
                )
            ),
            bounds=bounds,
        )
        self._dirty_region = DirtyRegion(x, y, w, h)
        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        from .native_cmyk.selection_commands import revert  # noqa: PLC0415

        native = revert(self, canvas)
        if native is not None:
            return native
        if not self._before_selection or self._bounds is None or self._layer_ref is None:
            return False
        x, y, w, h = self._bounds
        layer = self._layer_ref
        if _is_qimage_layer(layer):
            image = _ensure_qimage_premultiplied(layer)
            if image is None or self._before_layer is None:
                return False
            try:
                from PySide6.QtGui import QPainter  # noqa: PLC0415
            except ImportError:
                return False
            offset_x, offset_y = _layer_offset(layer)
            painter = QPainter(image)
            painter.drawImage(int(x - offset_x), int(y - offset_y), self._before_layer)
            painter.end()
        else:
            buffer = getattr(layer, "buffer", None)
            if not isinstance(buffer, np.ndarray) or self._before_layer is None:
                return False
            buffer[y : y + h, x : x + w] = self._before_layer
            if hasattr(layer, "mark_dirty"):
                layer.mark_dirty(x, y, w, h)

        UpdateSelectionMaskCommand._set_selection(
            canvas,
            UpdateSelectionMaskCommand._clone_mask(self._before_selection.mask),
            self._before_selection.bounds,
        )
        _set_selection_full_bounds(canvas, self._before_full_bounds)
        _set_selection_full_mask(canvas, self._before_full_mask)
        self._dirty_region = DirtyRegion(x, y, w, h)
        self._executed = False
        return True

    def _payload_mask_numpy(self) -> NDArray[Any] | None:
        mask = self.payload.mask
        if isinstance(mask, np.ndarray):
            return mask.astype(np.uint8, copy=False)
        try:
            from PySide6.QtGui import QImage  # noqa: PLC0415
        except ImportError:
            return None
        if isinstance(mask, QImage):
            return UpdateSelectionMaskCommand._mask_to_numpy(mask, mask.width(), mask.height())
        return None

    def _payload_image_numpy(self, *, premultiplied: bool) -> NDArray[Any] | None:
        image = self.payload.image
        if isinstance(image, np.ndarray):
            data = image.astype(np.uint8, copy=False)
            if premultiplied:
                return _numpy_rgba_to_bgra(_premultiply_rgba(data))
            return data
        try:
            from PySide6.QtGui import QImage  # noqa: PLC0415
        except ImportError:
            return None
        if isinstance(image, QImage):
            qimage = image
            if premultiplied and qimage.format() != QImage.Format.Format_ARGB32_Premultiplied:
                qimage = qimage.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            elif not premultiplied and qimage.format() != QImage.Format.Format_ARGB32:
                qimage = qimage.convertToFormat(QImage.Format.Format_ARGB32)
            qimage_data = _qimage_to_numpy_copy(qimage)
            if qimage_data is None:
                return None
            return qimage_data if premultiplied else _numpy_bgra_to_rgba(qimage_data)
        return None


class TransformSelectionCommand(Command):
    """Transform a selection payload and composite it into a layer."""

    def __init__(
        self,
        payload: SelectionPayload,
        transform: Affine2D,
        *,
        duplicate: bool = False,
        mask_mode: str = "soft",
        layer_id: str | None = None,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.payload = payload
        self.transform = transform
        self.duplicate = bool(duplicate)
        self.mask_mode = self._normalize_mask_mode(mask_mode)
        self.layer_id = layer_id
        self._before_selection: SelectionSnapshot | None = None
        self._after_selection: SelectionSnapshot | None = None
        self._before_full_bounds: SelectionRect | None = None
        self._before_full_mask: Any | None = None
        self._before_layer: Any | None = None
        self._after_layer: Any | None = None
        self._bounds: SelectionRect | None = None
        self._layer_ref: Any | None = None

    def apply(self, canvas: Canvas) -> bool:  # noqa: PLR0911, PLR0912, PLR0915
        from .native_cmyk.selection_commands import apply  # noqa: PLC0415

        native = apply(self, canvas)
        if native is not None:
            return native
        width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
        if width <= 0 or height <= 0:
            return False
        if self.payload is None:
            return False
        layer = _resolve_target_layer(canvas, self.layer_id)
        if layer is None or not _layer_allows_edit(layer):
            return False
        supports_expand = _supports_layer_expansion(canvas, layer)

        premultiplied = _is_qimage_layer(layer)
        dest_image = None
        dest_mask = None
        dest_bounds = None
        # GPU path (tier 1)
        warp_gpu = getattr(canvas, "apply_selection_transform_gpu", None)
        if callable(warp_gpu):
            transformed_gpu = warp_gpu(
                self.payload,
                self.transform,
                premultiplied=premultiplied,
                mask_mode=self.mask_mode,
                canvas_size=None if supports_expand else (width, height),
            )
            if transformed_gpu is not None:
                dest_image_qt, dest_mask_qt, dest_bounds = transformed_gpu
                dest_image = _qimage_to_numpy_copy(dest_image_qt)
                if dest_image is not None:
                    dest_mask = UpdateSelectionMaskCommand._mask_to_numpy(
                        dest_mask_qt, dest_mask_qt.width(), dest_mask_qt.height()
                    )
        # QPainter CPU fallback (tier 2)
        if (dest_image is None or dest_mask is None) and premultiplied:
            transformed_qt = _transform_payload_qimage(
                self.payload,
                self.transform,
                premultiplied=True,
                mask_mode=self.mask_mode,
                canvas_size=None if supports_expand else (width, height),
            )
            if transformed_qt is not None:
                dest_image_qt, dest_mask_qt, dest_bounds = transformed_qt
                dest_image = _qimage_to_numpy_copy(dest_image_qt)
                if dest_image is not None:
                    dest_mask = UpdateSelectionMaskCommand._mask_to_numpy(
                        dest_mask_qt, dest_mask_qt.width(), dest_mask_qt.height()
                    )
        if dest_image is None or dest_mask is None or dest_bounds is None:
            transformed = _transform_payload_raster(
                self.payload,
                self.transform,
                premultiplied=premultiplied,
                mask_mode=self.mask_mode,
                canvas_size=(width, height),
            )
            if transformed is None:
                return False
            dest_image, dest_mask, dest_bounds = transformed

        orig_bounds: SelectionRect | None
        union_bounds: SelectionRect | None
        if supports_expand:
            orig_bounds = self.payload.bounds
            union_bounds = _union_bounds(orig_bounds, dest_bounds)
        else:
            orig_bounds = _clip_bounds_to_canvas(canvas, self.payload.bounds)
            union_bounds = _union_bounds(orig_bounds, dest_bounds)
            union_bounds = _clip_bounds_to_canvas(canvas, union_bounds) if union_bounds else None
        if union_bounds is None:
            return False

        self._before_full_bounds = _get_selection_full_bounds(canvas)
        old_mask, old_bounds = UpdateSelectionMaskCommand._read_selection(canvas)
        self._before_selection = SelectionSnapshot(
            mask=UpdateSelectionMaskCommand._clone_mask(old_mask),
            bounds=old_bounds,
        )
        self._layer_ref = layer
        self._bounds = union_bounds

        if _supports_layer_expansion(canvas, layer):
            _ensure_layer_bounds(canvas, layer, union_bounds)

        if premultiplied:
            image = _ensure_qimage_premultiplied(layer)
            if image is None:
                return False
            try:
                from PySide6.QtCore import QRect  # noqa: PLC0415
            except ImportError:
                return False
            offset_x, offset_y = _layer_offset(layer)
            ux, uy, uw, uh = union_bounds
            rect = QRect(int(ux - offset_x), int(uy - offset_y), int(uw), int(uh))
            self._before_layer = image.copy(rect)
            view = _qimage_to_numpy_view(image)
            if view is None:
                return False
            if not self.duplicate and orig_bounds is not None:
                ox, oy, ow, oh = orig_bounds
                mask_full = _selection_payload_mask_numpy(self.payload)
                if mask_full is not None:
                    if self.mask_mode == "binary":
                        mask_full = np.where(mask_full > 127, 255, 0).astype(np.uint8, copy=False)
                    src_x = max(0, ox - self.payload.bounds[0])
                    src_y = max(0, oy - self.payload.bounds[1])
                    mask_patch = mask_full[src_y : src_y + oh, src_x : src_x + ow]
                    rx = int(ox - offset_x)
                    ry = int(oy - offset_y)
                    region = view[ry : ry + oh, rx : rx + ow]
                    factor = (255.0 - mask_patch.astype(np.float32)) / 255.0
                    region[:] = np.clip(region.astype(np.float32) * factor[..., None], 0.0, 255.0)

            dx, dy, dw, dh = dest_bounds
            rx = int(dx - offset_x)
            ry = int(dy - offset_y)
            region = view[ry : ry + dh, rx : rx + dw]
            _composite_premultiplied(region, dest_image, dest_mask)
            self._after_layer = image.copy(rect)
        else:
            buffer = getattr(layer, "buffer", None)
            if not isinstance(buffer, np.ndarray) or buffer.ndim != 3:
                return False
            ux, uy, uw, uh = union_bounds
            region_union = buffer[uy : uy + uh, ux : ux + uw]
            self._before_layer = region_union.copy()
            if not self.duplicate and orig_bounds is not None:
                ox, oy, ow, oh = orig_bounds
                mask_full = _selection_payload_mask_numpy(self.payload)
                if mask_full is not None:
                    if self.mask_mode == "binary":
                        mask_full = np.where(mask_full > 127, 255, 0).astype(np.uint8, copy=False)
                    src_x = max(0, ox - self.payload.bounds[0])
                    src_y = max(0, oy - self.payload.bounds[1])
                    mask_patch = mask_full[src_y : src_y + oh, src_x : src_x + ow]
                    region_orig = buffer[oy : oy + oh, ox : ox + ow]
                    factor = (255.0 - mask_patch.astype(np.float32)) / 255.0
                    region_orig[:] = np.clip(
                        region_orig.astype(np.float32) * factor[..., None], 0.0, 255.0
                    )

            dx, dy, dw, dh = dest_bounds
            region_dest = buffer[dy : dy + dh, dx : dx + dw]
            _composite_nonpremultiplied(region_dest, dest_image, dest_mask)
            self._after_layer = region_union.copy()
            if hasattr(layer, "mark_dirty"):
                layer.mark_dirty(ux, uy, uw, uh)

        full_mask_np: NDArray[Any] | None = None
        if supports_expand:
            if isinstance(dest_mask, np.ndarray):
                full_mask_np = dest_mask.astype(np.uint8, copy=False)
            else:
                try:
                    mask_w = int(dest_mask.width())
                    mask_h = int(dest_mask.height())
                except Exception as exc:
                    logger.debug(
                        "TransformSelectionCommand: dest_mask size query failed: %s",
                        exc,
                    )
                    mask_w = 0
                    mask_h = 0
                if mask_w > 0 and mask_h > 0:
                    full_mask_np = UpdateSelectionMaskCommand._mask_to_numpy(
                        dest_mask, mask_w, mask_h
                    )

        mask_full, bounds, _ = _place_mask_on_canvas(
            dest_mask,
            position=(dest_bounds[0], dest_bounds[1]),
            width=width,
            height=height,
        )
        if bounds is None:
            UpdateSelectionMaskCommand._set_selection(canvas, None, None)
            _set_selection_full_bounds(canvas, None)
            _set_selection_full_mask(canvas, None)
            self._after_selection = SelectionSnapshot(mask=None, bounds=None)
        else:
            canvas_mask = UpdateSelectionMaskCommand._convert_mask_for_canvas(
                canvas, mask_full, width, height
            )
            UpdateSelectionMaskCommand._set_selection(canvas, canvas_mask, bounds)
            _set_selection_full_bounds(canvas, dest_bounds if supports_expand else bounds)
            if supports_expand and full_mask_np is not None:
                _set_selection_full_mask(canvas, full_mask_np)
            else:
                _set_selection_full_mask(canvas, None)
            self._after_selection = SelectionSnapshot(
                mask=UpdateSelectionMaskCommand._clone_mask(canvas_mask),
                bounds=bounds,
            )

        clipped_dirty = _clip_bounds_to_canvas(canvas, union_bounds)
        if clipped_dirty is None:
            self._dirty_region = DirtyRegion(0, 0, width, height)
        else:
            self._dirty_region = DirtyRegion(*clipped_dirty)
        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        from .native_cmyk.selection_commands import revert  # noqa: PLC0415

        native = revert(self, canvas)
        if native is not None:
            return native
        if not self._before_selection or self._bounds is None or self._layer_ref is None:
            return False
        x, y, w, h = self._bounds
        layer = self._layer_ref
        if _is_qimage_layer(layer):
            image = _ensure_qimage_premultiplied(layer)
            if image is None or self._before_layer is None:
                return False
            try:
                from PySide6.QtGui import QPainter  # noqa: PLC0415
            except ImportError:
                return False
            offset_x, offset_y = _layer_offset(layer)
            painter = QPainter(image)
            painter.drawImage(int(x - offset_x), int(y - offset_y), self._before_layer)
            painter.end()
        else:
            buffer = getattr(layer, "buffer", None)
            if not isinstance(buffer, np.ndarray) or self._before_layer is None:
                return False
            buffer[y : y + h, x : x + w] = self._before_layer
            if hasattr(layer, "mark_dirty"):
                layer.mark_dirty(x, y, w, h)

        UpdateSelectionMaskCommand._set_selection(
            canvas,
            UpdateSelectionMaskCommand._clone_mask(self._before_selection.mask),
            self._before_selection.bounds,
        )
        _set_selection_full_bounds(canvas, self._before_full_bounds)
        _set_selection_full_mask(canvas, self._before_full_mask)
        self._dirty_region = DirtyRegion(x, y, w, h)
        self._executed = False
        return True

    @staticmethod
    def _normalize_mask_mode(value: str) -> str:
        lower = str(value).strip().lower()
        if lower in {"binary", "soft"}:
            return lower
        return "soft"
