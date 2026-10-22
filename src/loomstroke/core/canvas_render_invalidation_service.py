"""Render invalidation helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, cast

from PySide6.QtCore import QRect

from loomstroke.core.canvas_invalidation import (
    GpuResourceInvalidation,
    RenderInvalidation,
)

logger = logging.getLogger("loomstroke.core.canvas_service_simple")


class CanvasRenderInvalidationService:
    """Coordinate renderer invalidation state without owning canvas state."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner
        self._story_preview_layers: set[str] = set()

    def set_gpu_rendering(self, enabled: bool) -> None:
        """Enable or disable GPU-accelerated rendering."""
        self._owner._gpu_rendering = enabled

    def defer_recompose(self) -> None:
        """Defer recompose calls until the matching resume call."""
        self._owner._defer_recompose_depth += 1

    def resume_recompose(self) -> None:
        """Resume recompose after a deferred block."""
        if self._owner._defer_recompose_depth <= 0:
            return
        self._owner._defer_recompose_depth -= 1
        if self._owner._defer_recompose_depth == 0 and self._owner._defer_recompose_needed:
            self._owner._defer_recompose_needed = False
            self._owner._recompose()

    def bump_active_texture_version(self) -> None:
        """Increment the active *top-level* layer texture version.

        Deliberately indexes the root list rather than resolving the active
        layer by id: compositor textures are keyed per root entry, so when a
        nested layer is active it is that layer's root ancestor whose texture
        has to be invalidated.
        """
        if 0 <= self._owner._active_index < len(self._owner._layers):
            self._owner._layers[self._owner._active_index].texture_version += 1

    def bump_layer_texture_versions(self, layer_ids: Sequence[str]) -> None:
        """Increment texture versions by layer ID."""
        for layer_id in layer_ids:
            layer = self._owner._find_layer_by_id_tree(str(layer_id))
            if layer is not None:
                layer.texture_version += 1

    def recompose_region(self, dirty: QRect) -> None:
        """Accumulate a dirty rect for the GPU sub-upload path."""

        canvas_rect = QRect(0, 0, self._owner._width, self._owner._height)
        region = dirty.intersected(canvas_rect)
        if not region.isEmpty():
            if self._owner._gpu_dirty_rect is None:
                self._owner._gpu_dirty_rect = QRect(region)
            else:
                self._owner._gpu_dirty_rect = self._owner._gpu_dirty_rect.united(region)
        self._owner.composited_changed.emit(self._owner._composited)

    def notify_vector_preview_changed(self) -> None:
        """Bump vector preview texture versions and notify observers."""

        lid = getattr(self._owner, "_vector_preview_layer_id", None)
        from .japanese_text.story_preview import preview_layer_ids  # noqa: PLC0415

        current = preview_layer_ids(self._owner)
        for layer_id in (self._story_preview_layers | current) - {lid}:
            layer = self._owner._get_layer_by_id(layer_id)
            if layer is not None:
                layer.texture_version += 1
        self._story_preview_layers = current
        bumped = False
        if lid:
            layer = self._owner._get_layer_by_id(lid)
            if layer is not None:
                layer.texture_version += 1
                bumped = True
        from .path_text.metadata import DRAFT_PREVIEW_LAYER_ID  # noqa: PLC0415

        if not bumped and lid != DRAFT_PREVIEW_LAYER_ID:
            for layer in self._owner._layers:
                if layer.layer_type == "vector":
                    layer.texture_version += 1
        self._owner.composited_changed.emit(self._owner._composited)

    def recompose(self) -> None:
        """Notify the GPU compositor that a full recompose is needed."""

        if self._owner._defer_recompose_depth > 0:
            self._owner._defer_recompose_needed = True
            return
        self._owner._gpu_full_recompose = True
        self._owner.composited_changed.emit(self._owner._composited)

    def recompose_now(self) -> None:
        """Synchronous recompose alias in GPU mode."""

        if self._owner._defer_recompose_depth > 0:
            self._owner._defer_recompose_needed = True
            return
        self._owner._gpu_full_recompose = True
        self._owner.composited_changed.emit(self._owner._composited)

    def consume_render_invalidation(self) -> RenderInvalidation:
        """Return and clear renderer-neutral invalidation for one paint pass."""

        dirty_rect = (
            QRect(self._owner._gpu_dirty_rect) if self._owner._gpu_dirty_rect is not None else None
        )
        invalidation = RenderInvalidation(
            dirty_rect=dirty_rect,
            full_recompose=bool(self._owner._gpu_full_recompose),
            mask_dirty_layer_ids=frozenset(self._owner._gpu_mask_dirty_layers),
            gpu_resources=self._owner._gpu_resource_invalidation,
        )
        self._owner._gpu_dirty_rect = None
        self._owner._gpu_full_recompose = False
        self._owner._gpu_mask_dirty_layers = set()
        self._owner._gpu_resource_invalidation = GpuResourceInvalidation.empty()
        return invalidation

    def consume_gpu_dirty_rect(self) -> QRect | None:
        """Return and clear the accumulated GPU dirty rect."""
        rect = cast("QRect | None", self._owner._gpu_dirty_rect)
        self._owner._gpu_dirty_rect = None
        return rect

    def consume_gpu_full_recompose(self) -> bool:
        """Return and clear the full-recompose flag for GPU mode."""
        flag = cast("bool", self._owner._gpu_full_recompose)
        self._owner._gpu_full_recompose = False
        return flag

    def consume_gpu_mask_dirty_layers(self) -> set[str]:
        """Return and clear the set of layer IDs with dirty mask textures."""
        layers = cast("set[str]", self._owner._gpu_mask_dirty_layers)
        self._owner._gpu_mask_dirty_layers = set()
        return layers

    def invalidate_gpu_caches(self) -> None:
        """Invalidate all GPU-side caches after a graphics context loss."""
        self._owner._gpu_full_recompose = True
        self._owner._gpu_dirty_rect = None
        self._owner._gpu_mask_dirty_layers.clear()
        self._owner._gpu_resource_invalidation = self._owner._gpu_resource_invalidation.merge(
            GpuResourceInvalidation.for_all(reason="gl_context_loss")
        )
        layers = self._owner._flatten_layers()
        for layer in layers:
            layer.texture_version += 1
        logger.info("GPU caches invalidated after context loss (%d layers)", len(layers))
