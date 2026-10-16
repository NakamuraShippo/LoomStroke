"""Image and filter commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from .command_types import BlendMode, Command, DirtyRegion

logger = logging.getLogger("loomstroke.core.commands")

if TYPE_CHECKING:
    from .canvas import Canvas
    from .layer import Layer


class FilterCommand(Command):
    """
    Filter application command.
    Applies image filters to layers or selections.
    """

    def __init__(
        self,
        filter_type: str,
        params: dict[str, Any],
        layer_id: str | None = None,
        selection: DirtyRegion | None = None,
    ) -> None:
        """
        Initialize filter command.

        Args:
            filter_type: Type of filter to apply
            params: Filter parameters
            layer_id: Target layer ID (None for active layer)
            selection: Optional selection region
        """
        super().__init__()
        self.filter_type = filter_type
        self.params = params
        self.layer_id = layer_id
        self.selection = selection
        self._backup_data: NDArray[Any] | None = None
        self._backup_region: tuple[int, int, int, int] | None = None

    def apply(self, canvas: Canvas) -> bool:
        """Apply filter to canvas."""
        layer = self._get_target_layer(canvas)
        if not layer:
            return False

        # Determine region to filter
        if self.selection:
            min_x, min_y = self.selection.x, self.selection.y
            max_x = min(layer.width, min_x + self.selection.width)
            max_y = min(layer.height, min_y + self.selection.height)
        else:
            min_x, min_y = 0, 0
            max_x, max_y = layer.width, layer.height

        selection_mask = None
        selection_bounds = None
        raw_mask = getattr(canvas, "_selection_mask", None)
        raw_bounds = getattr(canvas, "_selection_bounds", None)
        if isinstance(raw_mask, np.ndarray) and isinstance(raw_bounds, tuple):
            selection_mask = raw_mask
            selection_bounds = raw_bounds
        if selection_mask is not None and selection_bounds is not None:
            sel_x, sel_y, sel_w, sel_h = selection_bounds
            min_x = max(min_x, sel_x)
            min_y = max(min_y, sel_y)
            max_x = min(max_x, sel_x + sel_w)
            max_y = min(max_y, sel_y + sel_h)
            if min_x >= max_x or min_y >= max_y:
                return False

        mask_patch = None
        if selection_mask is not None:
            mask_patch = selection_mask[min_y:max_y, min_x:max_x]
            if np.all(mask_patch <= 0):
                return False

        # Store backup
        self._backup_region = (min_x, min_y, max_x, max_y)
        self._backup_data = layer.buffer[min_y:max_y, min_x:max_x].copy()

        # Apply filter (simplified for PR#1)
        self._apply_filter_to_region(layer, min_x, min_y, max_x, max_y)

        if mask_patch is not None and self._backup_data is not None:
            region = layer.buffer[min_y:max_y, min_x:max_x].astype(np.float32)
            original = self._backup_data.astype(np.float32)
            mask_alpha = mask_patch.astype(np.float32) / 255.0
            region[:, :, :3] = (
                original[:, :, :3] * (1.0 - mask_alpha[..., None])
                + region[:, :, :3] * mask_alpha[..., None]
            )
            region[:, :, 3] = original[:, :, 3] * (1.0 - mask_alpha) + region[:, :, 3] * mask_alpha
            layer.buffer[min_y:max_y, min_x:max_x] = np.clip(region, 0.0, 255.0).astype(np.uint8)

        # Mark dirty
        self._dirty_region = DirtyRegion(min_x, min_y, max_x - min_x, max_y - min_y)
        layer.mark_dirty(min_x, min_y, max_x - min_x, max_y - min_y)

        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        """Revert filter from canvas."""
        if not self._executed or self._backup_data is None or self._backup_region is None:
            return False

        layer = self._get_target_layer(canvas)
        if not layer:
            return False

        min_x, min_y, max_x, max_y = self._backup_region
        layer.buffer[min_y:max_y, min_x:max_x] = self._backup_data
        layer.mark_dirty(min_x, min_y, max_x - min_x, max_y - min_y)

        self._executed = False
        return True

    def _get_target_layer(self, canvas: Canvas) -> Layer | None:
        """Get the target layer for this command."""
        if self.layer_id:
            return canvas.get_layer_by_id(self.layer_id)
        return canvas.active_layer

    def _apply_filter_to_region(
        self, layer: Layer, min_x: int, min_y: int, max_x: int, max_y: int
    ) -> None:
        """Apply filter to region (placeholder for PR#1)."""
        # Simplified filter implementations for PR#1
        region = layer.buffer[min_y:max_y, min_x:max_x]

        if self.filter_type == "blur":
            # Simple box blur placeholder
            self.params.get("radius", 3)
            # Actual implementation would use proper convolution
            pass
        elif self.filter_type == "brightness":
            factor = self.params.get("factor", 1.0)
            region[:, :, :3] = np.clip(region[:, :, :3] * factor, 0, 255)
        elif self.filter_type == "contrast":
            factor = self.params.get("factor", 1.0)
            mean = region[:, :, :3].mean()
            region[:, :, :3] = np.clip((region[:, :, :3] - mean) * factor + mean, 0, 255)


class TransformCommand(Command):
    """
    Transform command for layer transformations.
    Handles move, scale, rotate operations.
    """

    def __init__(
        self, transform_type: str, params: dict[str, Any], layer_id: str | None = None
    ) -> None:
        """
        Initialize transform command.

        Args:
            transform_type: Type of transform (move, scale, rotate, flip)
            params: Transform parameters
            layer_id: Target layer ID (None for active layer)
        """
        super().__init__()
        self.transform_type = transform_type
        self.params = params
        self.layer_id = layer_id
        self._backup_data: NDArray[Any] | None = None
        self._backup_transform: dict[str, Any] | None = None

    def apply(self, canvas: Canvas) -> bool:
        """Apply transform to canvas."""
        layer = self._get_target_layer(canvas)
        if not layer:
            return False

        # Store backup of entire layer for transforms
        self._backup_data = layer.buffer.copy()

        # Apply transform based on type
        if self.transform_type == "move":
            dx = self.params.get("dx", 0)
            dy = self.params.get("dy", 0)
            self._apply_move(layer, dx, dy)
        elif self.transform_type == "scale":
            sx = self.params.get("sx", 1.0)
            sy = self.params.get("sy", 1.0)
            self._apply_scale(layer, sx, sy)
        elif self.transform_type == "rotate":
            angle = self.params.get("angle", 0.0)
            self._apply_rotate(layer, angle)
        elif self.transform_type == "flip":
            horizontal = self.params.get("horizontal", False)
            vertical = self.params.get("vertical", False)
            self._apply_flip(layer, horizontal, vertical)

        # Mark entire layer as dirty for transforms
        self._dirty_region = DirtyRegion(0, 0, layer.width, layer.height)
        layer.mark_dirty(0, 0, layer.width, layer.height)

        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        """Revert transform from canvas."""
        if not self._executed or self._backup_data is None:
            return False

        layer = self._get_target_layer(canvas)
        if not layer:
            return False

        layer.buffer[:] = self._backup_data
        layer.mark_dirty(0, 0, layer.width, layer.height)

        self._executed = False
        return True

    def _get_target_layer(self, canvas: Canvas) -> Layer | None:
        """Get the target layer for this command."""
        if self.layer_id:
            return canvas.get_layer_by_id(self.layer_id)
        return canvas.active_layer

    def _apply_move(self, layer: Layer, dx: int, dy: int) -> None:
        """Apply move transform."""
        if dx == 0 and dy == 0:
            return

        new_buffer = np.zeros_like(layer.buffer)

        # Calculate source and destination regions
        src_x1 = max(0, -dx)
        src_y1 = max(0, -dy)
        src_x2 = min(layer.width, layer.width - dx)
        src_y2 = min(layer.height, layer.height - dy)

        dst_x1 = max(0, dx)
        dst_y1 = max(0, dy)

        # Copy shifted region
        if src_x2 > src_x1 and src_y2 > src_y1:
            new_buffer[dst_y1 : dst_y1 + (src_y2 - src_y1), dst_x1 : dst_x1 + (src_x2 - src_x1)] = (
                layer.buffer[src_y1:src_y2, src_x1:src_x2]
            )

        layer.buffer[:] = new_buffer

    def _apply_scale(self, layer: Layer, sx: float, sy: float) -> None:
        """Apply scale transform using cv2 for high-quality interpolation."""
        import cv2  # noqa: PLC0415

        if abs(sx - 1.0) < 1e-6 and abs(sy - 1.0) < 1e-6:
            return

        h, w = layer.buffer.shape[:2]
        new_w = max(1, round(w * sx))
        new_h = max(1, round(h * sy))

        # Scale the content
        buf = layer.buffer
        if buf.dtype == np.float32:
            scaled = cv2.resize(buf, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        else:
            scaled = cv2.resize(buf, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        # Place scaled content back into original-size buffer, centered
        new_buffer = np.zeros_like(layer.buffer)
        ox = max(0, (w - new_w) // 2)
        oy = max(0, (h - new_h) // 2)
        sx_crop = max(0, (new_w - w) // 2)
        sy_crop = max(0, (new_h - h) // 2)
        copy_w = min(new_w - sx_crop, w - ox)
        copy_h = min(new_h - sy_crop, h - oy)
        if copy_w > 0 and copy_h > 0:
            new_buffer[oy : oy + copy_h, ox : ox + copy_w] = scaled[
                sy_crop : sy_crop + copy_h, sx_crop : sx_crop + copy_w
            ]
        layer.buffer[:] = new_buffer

    def _apply_rotate(self, layer: Layer, angle: float) -> None:
        """Apply rotation transform using cv2 warpAffine."""
        import cv2  # noqa: PLC0415

        if abs(angle) < 1e-6:
            return

        h, w = layer.buffer.shape[:2]
        center = (w / 2.0, h / 2.0)
        rotation_matrix = cv2.getRotationMatrix2D(center, -np.degrees(angle), 1.0)

        buf = layer.buffer
        rotated = cv2.warpAffine(
            buf,
            rotation_matrix,
            (w, h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0, 0),
        )
        layer.buffer[:] = rotated

    def _apply_flip(self, layer: Layer, horizontal: bool, vertical: bool) -> None:
        """Apply flip transform."""
        if horizontal:
            layer.buffer[:] = np.fliplr(layer.buffer)
        if vertical:
            layer.buffer[:] = np.flipud(layer.buffer)


class ApplyImageCommand(Command):
    """Command that applies an image onto the canvas.

    This lightweight implementation acts as a compatibility shim for
    integrations that expect the legacy command to exist. The actual
    image blending is delegated to the canvas implementation when
    available; otherwise the command is effectively a no-op that still
    records application metadata for history/undo systems.
    """

    def __init__(
        self,
        image: NDArray[Any],
        *,
        position: tuple[int, int] = (0, 0),
        blend_mode: str = BlendMode.NORMAL.value,
        opacity: float = 1.0,
        metadata: dict[str, Any] | None = None,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.image = image
        self.position = position
        self.blend_mode = blend_mode
        self.opacity = opacity
        self.metadata = metadata or {}

    def apply(self, canvas: Canvas) -> bool:
        """Apply image data to the canvas.

        The actual blending behaviour depends on the canvas service.
        If the active canvas exposes a dedicated helper (e.g.
        ``apply_image``), it is invoked; otherwise the command records
        execution without modifying pixel data, ensuring compatibility
        for tests and consumers that only inspect metadata.
        """
        handler = getattr(canvas, "apply_image", None)
        if callable(handler):
            handler(
                self.image,
                position=self.position,
                blend_mode=self.blend_mode,
                opacity=self.opacity,
                metadata=self.metadata,
            )
        else:
            logger.debug(
                "ApplyImageCommand executed without canvas.apply_image support;"
                " metadata preserved only."
            )

        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        """Best-effort revert implementation.

        If the canvas exposes an ``undo_image`` helper it will be used;
        otherwise the command reports that it cannot revert. This keeps
        the shim inexpensive while signalling limitations clearly.
        """
        handler = getattr(canvas, "undo_image", None)
        if callable(handler):
            handler(
                position=self.position,
                metadata=self.metadata,
            )
            self._executed = False
            return True

        return False
