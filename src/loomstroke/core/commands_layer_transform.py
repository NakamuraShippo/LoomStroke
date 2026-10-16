"""Layer transform commands."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import numpy as np

from .command_image_utils import _affine_bounds, _is_qimage_layer, _union_bounds
from .command_layer_access import (
    _canvas_size_tuple,
    _coerce_layer_transform,
    _layer_allows_edit,
    _layer_transform,
    _resolve_layer_by_id,
    _resolve_target_layer,
    _set_layer_transform,
    _shift_layer_mask,
    _sync_layer_transform_translation,
)
from .command_types import Command, DirtyRegion, LayerTransform, SelectionRect
from .commands_selection_mask import UpdateSelectionMaskCommand, _clip_bounds_to_canvas
from .geometry import Affine2D

logger = logging.getLogger("loomstroke.core.commands")

if TYPE_CHECKING:
    from .canvas import Canvas


class TransformLayerCommand(Command):
    """Update a raster layer transform without resampling its pixels."""

    def __init__(
        self,
        new_transform: LayerTransform | Affine2D | Mapping[str, Any],
        *,
        layer_id: str | None = None,
        old_transform: LayerTransform | Affine2D | Mapping[str, Any] | None = None,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.new_transform = _coerce_layer_transform(new_transform)
        self.old_transform = (
            _coerce_layer_transform(old_transform) if old_transform is not None else None
        )
        self.transform = self.new_transform.to_affine()
        self.layer_id = layer_id
        self._bounds: SelectionRect | None = None
        self._layer_ref: Any | None = None

    def apply(self, canvas: Canvas) -> bool:
        layer = _resolve_target_layer(canvas, self.layer_id)
        if layer is None or not _layer_allows_edit(layer) or not _is_qimage_layer(layer):
            return False
        try:
            from PySide6.QtGui import QImage  # noqa: PLC0415
        except ImportError:
            return False
        image = getattr(layer, "image", None)
        if not isinstance(image, QImage) or image.isNull():
            return False

        width = int(image.width())
        height = int(image.height())
        if width <= 0 or height <= 0:
            return False

        self._layer_ref = layer
        current = _layer_transform(layer)
        before = self.old_transform or current
        self.old_transform = before
        # The translation is applied as a delta on top of the layer's
        # *current* translation rather than as the absolute value recorded
        # at drag time: painting after a move can expand the backing image
        # (``ensure_layer_image_bounds``), which shifts the local origin and
        # re-anchors the translation.  Restoring an absolute value would
        # leave the pixels displaced by that shift on undo/redo.
        target = self.new_transform.with_translation(
            current.tx + (self.new_transform.tx - before.tx),
            current.ty + (self.new_transform.ty - before.ty),
        )
        self._bounds = _union_bounds(
            _affine_bounds(current.to_affine(), width, height),
            _affine_bounds(target.to_affine(), width, height),
        )
        _set_layer_transform(layer, target)
        self._set_dirty_region(canvas)
        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        if not self._executed or self._layer_ref is None or self.old_transform is None:
            return False
        layer = self._layer_ref
        current = _layer_transform(layer)
        target = self.old_transform.with_translation(
            current.tx - (self.new_transform.tx - self.old_transform.tx),
            current.ty - (self.new_transform.ty - self.old_transform.ty),
        )
        image = getattr(layer, "image", None)
        if image is not None and not image.isNull():
            self._bounds = _union_bounds(
                _affine_bounds(current.to_affine(), image.width(), image.height()),
                _affine_bounds(target.to_affine(), image.width(), image.height()),
            )
        _set_layer_transform(layer, target)
        self._set_dirty_region(canvas)
        self._executed = False
        return True

    def _set_dirty_region(self, canvas: Canvas) -> None:
        clipped = _clip_bounds_to_canvas(canvas, self._bounds) if self._bounds else None
        if clipped is None:
            self._dirty_region = DirtyRegion(0, 0, *UpdateSelectionMaskCommand._canvas_size(canvas))
        else:
            self._dirty_region = DirtyRegion(*clipped)


class LayerVisibilityCommand(Command):
    """Toggle layer visibility with undo/redo support."""

    def __init__(self, layer_id: str, visible: bool) -> None:
        super().__init__(merge_key=None)
        self.layer_id = layer_id
        self._new_visible = visible
        self._old_visible: bool | None = None

    def apply(self, canvas: Canvas) -> bool:
        layer = _resolve_layer_by_id(canvas, self.layer_id)
        if layer is None:
            return False
        self._old_visible = bool(getattr(layer, "visible", True))
        if self._old_visible == self._new_visible:
            return False
        layer.visible = self._new_visible
        from .comic_panels.panel_gate import refresh_shared_masks  # noqa: PLC0415

        if callable(getattr(canvas, "_layer_tree", None)):
            refresh_shared_masks(canvas)
        return True

    def revert(self, canvas: Canvas) -> bool:
        if self._old_visible is None:
            return False
        layer = _resolve_layer_by_id(canvas, self.layer_id)
        if layer is None:
            return False
        layer.visible = self._old_visible
        from .comic_panels.panel_gate import refresh_shared_masks  # noqa: PLC0415

        if callable(getattr(canvas, "_layer_tree", None)):
            refresh_shared_masks(canvas)
        return True


class MoveLayerCommand(Command):
    """Move a layer by updating its offset or shifting its buffer."""

    def __init__(
        self,
        dx: int,
        dy: int,
        *,
        layer_id: str | None = None,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.dx = int(dx)
        self.dy = int(dy)
        self.layer_id = layer_id
        self._before_offset: tuple[int, int] | None = None
        self._after_offset: tuple[int, int] | None = None
        self._before_layer: Any | None = None
        self._layer_ref: Any | None = None
        self._child_before_offsets: list[tuple[Any, int, int]] = []
        self._vec_before: list[tuple[Any, int, Any]] = []  # (layer, index, original_obj)
        self._vec_mask_shifted: list[Any] = []  # vector children whose masks moved
        self._mask_only: bool = False  # True when only the mask was shifted (vector/effect)

    def _restore_offsets(
        self,
        layer: Any,
        x: int,
        y: int,
        *,
        phase: str,
        exc: BaseException,
    ) -> None:
        """Roll back partial offset updates after a setter raises.

        Reachable only when the layer class gains validation logic on its
        ``offset_x`` / ``offset_y`` setters (``__slots__``, ``@property``,
        GPU-backed writes, etc.).  Under the current dataclass-based layer
        implementation the setters cannot raise, so this helper is a
        forward-safety guard rather than an actively exercised path.
        """
        try:
            layer.offset_x = int(x)
            layer.offset_y = int(y)
        except Exception:
            logger.exception(
                "MoveLayerCommand.%s rollback failed for layer_id=%s",
                phase,
                self.layer_id,
            )
        else:
            logger.warning(
                "MoveLayerCommand.%s failed, state rolled back. layer_id=%s: %s",
                phase,
                self.layer_id,
                exc,
            )

    def apply(self, canvas: Canvas) -> bool:  # noqa: PLR0911, PLR0915
        if self.dx == 0 and self.dy == 0:
            return False
        layer = _resolve_target_layer(canvas, self.layer_id)
        if layer is None or not _layer_allows_edit(layer):
            return False
        self._layer_ref = layer

        # Group layer: move all children recursively
        if getattr(layer, "layer_type", "") == "group":
            return self._apply_group_move(canvas, layer)

        if _is_qimage_layer(layer) and hasattr(layer, "offset_x") and hasattr(layer, "offset_y"):
            image = getattr(layer, "image", None)
            if image is None or image.isNull():
                return False
            before_x = int(getattr(layer, "offset_x", 0))
            before_y = int(getattr(layer, "offset_y", 0))
            self._before_offset = (before_x, before_y)
            after_x = before_x + self.dx
            after_y = before_y + self.dy
            self._after_offset = (after_x, after_y)
            try:
                layer.offset_x = int(after_x)
                layer.offset_y = int(after_y)
            except Exception as exc:
                self._restore_offsets(layer, before_x, before_y, phase="apply", exc=exc)
                return False
            _sync_layer_transform_translation(layer)
            _shift_layer_mask(layer, self.dx, self.dy)

            width = int(image.width())
            height = int(image.height())
            old_bounds = (before_x, before_y, width, height)
            new_bounds = (after_x, after_y, width, height)
            union_bounds = _union_bounds(old_bounds, new_bounds)
            clipped = _clip_bounds_to_canvas(canvas, union_bounds) if union_bounds else None
            if clipped is None:
                self._dirty_region = DirtyRegion(
                    0, 0, *UpdateSelectionMaskCommand._canvas_size(canvas)
                )
            else:
                self._dirty_region = DirtyRegion(*clipped)
            self._executed = True
            return True

        # Mask-only shift for layers without movable image/buffer (vector/effect)
        if getattr(layer, "layer_mask", None) is not None:
            _shift_layer_mask(layer, self.dx, self.dy)
            self._mask_only = True
            cw, ch = _canvas_size_tuple(canvas)
            self._dirty_region = DirtyRegion(0, 0, cw, ch)
            self._executed = True
            return True

        buffer = getattr(layer, "buffer", None)
        if not isinstance(buffer, np.ndarray) or buffer.ndim != 3:
            return False
        self._before_layer = buffer.copy()
        height, width = buffer.shape[:2]
        new_buffer = np.zeros_like(buffer)
        src_x1 = max(0, -self.dx)
        src_y1 = max(0, -self.dy)
        src_x2 = min(width, width - self.dx)
        src_y2 = min(height, height - self.dy)
        dst_x1 = max(0, self.dx)
        dst_y1 = max(0, self.dy)
        if src_x2 > src_x1 and src_y2 > src_y1:
            new_buffer[dst_y1 : dst_y1 + (src_y2 - src_y1), dst_x1 : dst_x1 + (src_x2 - src_x1)] = (
                buffer[src_y1:src_y2, src_x1:src_x2]
            )
        buffer[:] = new_buffer
        if hasattr(layer, "mark_dirty"):
            layer.mark_dirty(0, 0, width, height)
        self._dirty_region = DirtyRegion(0, 0, width, height)
        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:  # noqa: PLR0911
        if not self._executed or self._layer_ref is None:
            return False
        layer = self._layer_ref
        if getattr(layer, "layer_type", "") == "group" and (
            self._child_before_offsets or self._vec_before or self._vec_mask_shifted
        ):
            return self._revert_group_move()
        if self._mask_only:
            _shift_layer_mask(layer, -self.dx, -self.dy)
            cw, ch = _canvas_size_tuple(canvas)
            self._dirty_region = DirtyRegion(0, 0, cw, ch)
            self._executed = False
            return True
        if _is_qimage_layer(layer) and self._before_offset is not None:
            # Undo by delta from the *current* offset, not the absolute
            # pre-apply value: a paint after the move may have expanded the
            # backing image (``ensure_layer_image_bounds``) and re-anchored
            # the offset, so the recorded absolute value no longer maps the
            # pixels back to where they were.
            current_x = int(getattr(layer, "offset_x", self._before_offset[0]))
            current_y = int(getattr(layer, "offset_y", self._before_offset[1]))
            before_x = current_x - self.dx
            before_y = current_y - self.dy
            try:
                layer.offset_x = int(before_x)
                layer.offset_y = int(before_y)
            except Exception as exc:
                self._restore_offsets(layer, current_x, current_y, phase="revert", exc=exc)
                return False
            _sync_layer_transform_translation(layer)
            _shift_layer_mask(layer, -self.dx, -self.dy)
            image = getattr(layer, "image", None)
            if image is not None and not image.isNull():
                width = int(image.width())
                height = int(image.height())
                old_bounds = (before_x, before_y, width, height)
                new_bounds = (current_x, current_y, width, height)
                union_bounds = _union_bounds(old_bounds, new_bounds)
                clipped = _clip_bounds_to_canvas(canvas, union_bounds) if union_bounds else None
                if clipped is None:
                    self._dirty_region = DirtyRegion(
                        0, 0, *UpdateSelectionMaskCommand._canvas_size(canvas)
                    )
                else:
                    self._dirty_region = DirtyRegion(*clipped)
            self._executed = False
            return True

        buffer = getattr(layer, "buffer", None)
        if not isinstance(buffer, np.ndarray) or self._before_layer is None:
            return False
        buffer[:] = self._before_layer
        if hasattr(layer, "mark_dirty"):
            height, width = buffer.shape[:2]
            layer.mark_dirty(0, 0, width, height)
        self._executed = False
        return True

    # --- Group move helpers ---

    def _collect_children(self, layer: Any) -> list[Any]:
        """Recursively collect all descendant layers with offsets."""
        result: list[Any] = []
        for child in getattr(layer, "children", []):
            if hasattr(child, "offset_x"):
                result.append(child)
            result.extend(self._collect_children(child))
        return result

    def _apply_group_move(self, canvas: Canvas, group: Any) -> bool:
        """Move a group layer by shifting all children's offsets and vectors.

        Vector children move via their anchors (the document source of
        truth); shifting their offsets as well would double-apply the move
        in renderers that honor vector-layer offsets (wgpu draw region and
        the direct vector path).  Their masks still shift with the move.
        """
        children = self._collect_children(group)
        self._child_before_offsets = []
        self._vec_mask_shifted = []
        for child in children:
            if getattr(child, "layer_type", "") == "vector":
                _shift_layer_mask(child, self.dx, self.dy)
                self._vec_mask_shifted.append(child)
                continue
            ox, oy = int(child.offset_x), int(child.offset_y)
            self._child_before_offsets.append((child, ox, oy))
            child.offset_x = int(ox + self.dx)
            child.offset_y = int(oy + self.dy)
            _sync_layer_transform_translation(child)
            _shift_layer_mask(child, self.dx, self.dy)
        # Translate vector objects in vector children
        self._vec_before = []
        for child in children:
            if getattr(child, "layer_type", "") != "vector":
                continue
            objects = getattr(child, "vector_objects", None)
            if not objects:
                continue
            for i, obj in enumerate(objects):
                self._vec_before.append((child, i, obj))
                objects[i] = _translate_vec_obj(obj, self.dx, self.dy)
        cw, ch = _canvas_size_tuple(canvas)
        self._dirty_region = DirtyRegion(0, 0, cw, ch)
        self._executed = True
        return True

    def _revert_group_move(self) -> bool:
        """Revert a group move by restoring all children's offsets and vectors."""
        for child, _ox, _oy in self._child_before_offsets:
            # Delta undo (see ``revert``): buffer expansion between apply
            # and revert re-anchors child offsets, so absolute restore
            # would displace the pixels.
            _shift_layer_mask(child, -self.dx, -self.dy)
            child.offset_x = int(child.offset_x) - self.dx
            child.offset_y = int(child.offset_y) - self.dy
            _sync_layer_transform_translation(child)
        for child in self._vec_mask_shifted:
            _shift_layer_mask(child, -self.dx, -self.dy)
        for child, i, original in self._vec_before:
            objects = getattr(child, "vector_objects", None)
            if objects is not None and i < len(objects):
                objects[i] = original
        cw = ch = 4096  # safe fallback
        self._dirty_region = DirtyRegion(0, 0, cw, ch)
        self._executed = False
        return True


def _translate_vec_obj(obj: Any, dx: float, dy: float) -> Any:
    """Translate a frozen VectorObject by (dx, dy) using duck-typed reconstruction."""
    try:
        from loomstroke.plugins.vector_tools.common import (  # noqa: PLC0415
            Anchor,
            VectorObject,
            VectorPath,
        )

        paths = []
        for path in obj.paths:
            anchors = [
                Anchor(
                    pos=(a.pos[0] + dx, a.pos[1] + dy),
                    kind=a.kind,
                    h_in=a.h_in,
                    h_out=a.h_out,
                )
                for a in path.anchors
            ]
            paths.append(VectorPath(anchors=anchors, closed=path.closed))
        meta = dict(obj.meta)
        # Shift parametric bbox if present
        param = meta.get("shape_manager_parametric")
        if isinstance(param, Mapping):
            p = dict(param)
            bbox = p.get("reference_bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                p["reference_bbox"] = [bbox[0] + dx, bbox[1] + dy, bbox[2] + dx, bbox[3] + dy]
                meta["shape_manager_parametric"] = p
        # Shift text frame if present
        text = meta.get("text_tool")
        if isinstance(text, Mapping):
            t = dict(text)
            frame = t.get("frame")
            if isinstance(frame, Mapping):
                f = dict(frame)
                if "x" in f:
                    f["x"] = float(f["x"]) + dx
                if "y" in f:
                    f["y"] = float(f["y"]) + dy
                t["frame"] = f
            if "x" in t:
                t["x"] = float(t["x"]) + dx
            if "y" in t:
                t["y"] = float(t["y"]) + dy
            meta["text_tool"] = t
        return VectorObject(id=obj.id, paths=paths, style=obj.style, meta=meta, preview=obj.preview)
    except Exception as exc:
        logger.debug("_translate_vec_obj: duck-typed rebuild failed, returning original: %s", exc)
        return obj
