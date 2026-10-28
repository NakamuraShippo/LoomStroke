"""Stroke pixel commit helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRect

from .rgb_eraser_coverage import (
    is_standard_zero_rgb_erase,
    paint_rgb_erase_coverage,
    supports_rgb_erase_coverage,
)


class CanvasStrokePixelCommitService:
    """Commit paint / erase stroke pixels and related history state."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def apply_erase_into_layer(
        self,
        *,
        layer: Any,
        points: list[Any],
        brush: Any,
        bounds: QRect,
        offset_x: int,
        offset_y: int,
    ) -> bool:
        owner = self._owner
        if is_standard_zero_rgb_erase(owner, brush):
            return True
        selection_mask, selection_bounds = owner._selection_clip_state()
        deferred = owner._txn_try_defer_paint(layer, bounds, offset_x, offset_y)

        local_bounds = QRect(bounds)
        local_bounds.translate(-offset_x, -offset_y)

        channels = owner.channels() if hasattr(owner, "channels") else None
        channel_before = channels.capture(layer, local_bounds) if channels else None

        if not deferred:
            assert layer.image is not None
            before_patch = layer.image.copy(local_bounds)

        base_size = max(1, int(getattr(brush, "size", 10)))

        masked = selection_mask is not None and selection_bounds is not None
        if supports_rgb_erase_coverage(owner, masked=masked):
            paint_rgb_erase_coverage(
                owner,
                layer=layer,
                points=points,
                brush=brush,
                bounds=bounds,
                local_bounds=local_bounds,
                offset_x=offset_x,
                offset_y=offset_y,
                selection_mask=selection_mask if masked else None,
            )
        elif masked:
            owner._paint_erased_masked_stroke(
                layer=layer,
                points=points,
                brush_size=base_size,
                bounds=bounds,
                local_bounds=local_bounds,
            )
        else:
            owner._paint_erased_clear_stroke(
                layer=layer,
                points=points,
                brush_size=base_size,
                offset_x=offset_x,
                offset_y=offset_y,
            )

        if channels:
            channels.finish(layer, local_bounds, channel_before, erase=True)
        layer.texture_version += 1

        if not deferred:
            assert layer.image is not None
            after_patch = layer.image.copy(local_bounds)
            metadata: dict[str, Any] = {"command": "erase"}
            if getattr(owner, "_mask_edit_flag", False):
                from .mask_coverage import normalize_mask_image  # noqa: PLC0415

                # The outer mask swap normalizes the live mask after this
                # capture. Store that same final form for an exact Redo.
                normalize_mask_image(after_patch)
                metadata["mask_edit"] = True
            owner._record_history_patch(
                "Erase Stroke",
                owner._history_patch_from_images(
                    layer.id,
                    QRect(bounds),
                    before_patch,
                    after_patch,
                    metadata,
                ),
            )
        owner._emit_layer_content_changed(layer, bounds, {"command": "erase"})
        return True

    def apply_paint_into_layer(
        self,
        *,
        layer: Any,
        points: list[Any],
        brush: Any,
        color: Any,
        bounds: QRect,
        offset_x: int,
        offset_y: int,
    ) -> bool:
        owner = self._owner
        selection_mask, selection_bounds = owner._selection_clip_state()
        deferred = owner._txn_try_defer_paint(layer, bounds, offset_x, offset_y)

        local_bounds = QRect(bounds)
        local_bounds.translate(-offset_x, -offset_y)

        if not deferred:
            assert layer.image is not None
            before_patch = layer.image.copy(local_bounds)

        saved_alpha = owner._save_alpha(layer, local_bounds)
        from .comic_panels.stroke_upload import (  # noqa: PLC0415
            begin_blank_upload,
            finish_blank_upload,
        )

        initial_blank = begin_blank_upload(layer)
        channels = owner.channels() if hasattr(owner, "channels") else None
        channel_before = channels.capture(layer, local_bounds) if channels else None

        if selection_mask is not None and selection_bounds is not None:
            owner._paint_into_selection(
                layer=layer,
                points=points,
                brush=brush,
                color=color,
                bounds=bounds,
                local_bounds=local_bounds,
            )
        else:
            owner._paint_into_layer(
                layer=layer,
                points=points,
                brush=brush,
                color=color,
                offset_x=offset_x,
                offset_y=offset_y,
            )

        owner._restore_alpha(layer, local_bounds, saved_alpha)
        if channels:
            channels.finish(layer, local_bounds, channel_before)
        layer.texture_version += 1
        finish_blank_upload(layer, bounds, initial_blank)

        if not deferred:
            assert layer.image is not None
            after_patch = layer.image.copy(local_bounds)
            metadata: dict[str, Any] = {"command": "paint"}
            if getattr(owner, "_mask_edit_flag", False):
                metadata["mask_edit"] = True
            owner._record_history_patch(
                "Paint Stroke",
                owner._history_patch_from_images(
                    layer.id,
                    QRect(bounds),
                    before_patch,
                    after_patch,
                    metadata,
                ),
            )
        owner._emit_layer_content_changed(layer, bounds, {"command": "paint"})
        return True
