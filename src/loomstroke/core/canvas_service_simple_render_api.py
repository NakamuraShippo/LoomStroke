"""Canvas service RenderApi mixin."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, TypeAlias

from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import (
    QImage,
    QPainter,
    QPainterPath,
    QPixmap,
)

from loomstroke.core.canvas_blend_service import CanvasBlendService
from loomstroke.core.canvas_invalidation import RenderInvalidation
from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.commands import (
    BlendMode,
)
from loomstroke.core.vector_text_render_service import VectorRasterResult, VectorTextRenderService
from loomstroke.host_api import LayerRef

logger = logging.getLogger(__name__)
_PEN_LAG_DIAG = os.environ.get("LOOMSTROKE_PEN_LAG_DIAG", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LayerValue: TypeAlias = LayerRef | Mapping[str, int] | bool | str | int | None


if TYPE_CHECKING:
    from loomstroke.core.canvas_service_simple_accessors import CanvasServiceAccessorsMixin

    class _CanvasServiceRenderApiBase(CanvasServiceAccessorsMixin):
        _layer_cache_version: int

else:

    class _CanvasServiceRenderApiBase:
        pass


class CanvasServiceRenderApiMixin(_CanvasServiceRenderApiBase):
    def set_gpu_rendering(self, enabled: bool) -> None:
        """Enable/disable GPU-accelerated rendering.

        When enabled, _recompose() becomes a lightweight notification
        instead of performing CPU compositing.
        """
        self._render_invalidation().set_gpu_rendering(enabled)

    def consume_render_invalidation(self) -> RenderInvalidation:
        """Return and clear renderer-neutral invalidation for one paint pass."""
        return self._render_invalidation().consume_render_invalidation()

    def consume_gpu_dirty_rect(self) -> QRect | None:
        """Return and clear the accumulated GPU dirty rect.

        Compatibility bridge for pre-boundary callers. New renderers should
        prefer :meth:`consume_render_invalidation`.
        """
        return self._render_invalidation().consume_gpu_dirty_rect()

    def consume_gpu_full_recompose(self) -> bool:
        """Return and clear the full-recompose flag for GPU mode.

        Compatibility bridge for pre-boundary callers.
        """
        return self._render_invalidation().consume_gpu_full_recompose()

    def consume_gpu_mask_dirty_layers(self) -> set[str]:
        """Return and clear the set of layer IDs with dirty mask textures.

        Compatibility bridge for pre-boundary callers.
        """
        return self._render_invalidation().consume_gpu_mask_dirty_layers()

    def invalidate_gpu_caches(self) -> None:
        """Invalidate all GPU-side caches after a GL context loss.

        Called by canvas_widget after Qt recreates the OpenGL context
        (e.g. sleep/resume, GPU driver TDR). Forces all layer textures
        to be re-uploaded from CPU-side QImage data on the next paint.
        """
        self._render_invalidation().invalidate_gpu_caches()

    def defer_recompose(self) -> None:
        """Defer recompose calls until ``resume_recompose`` is called.

        Nestable — each ``defer_recompose`` must be matched by a
        ``resume_recompose``.  A single ``_recompose`` is issued when the
        outermost scope resumes, provided at least one recompose was
        requested while deferred.
        """
        self._render_invalidation().defer_recompose()

    def resume_recompose(self) -> None:
        """Resume recompose after a ``defer_recompose`` block."""
        self._render_invalidation().resume_recompose()

    def _invalidate_layer_cache(self) -> None:
        """Bump composite version counter.

        Historically rebuilt CPU below/above pixmap caches; under GPU-unified
        rendering (Phase 5c) this simply bumps ``_layer_cache_version`` which
        external observers (layer_panel thumbnail cache) consume as a change
        signal.
        """
        self._layer_cache_version += 1

    def _recompose_region(self, dirty: QRect) -> None:
        """Accumulate a dirty rect for the GPU sub-upload path.

        Under GPU-unified rendering (Phase 3+) the compositor consumes the
        accumulated ``_gpu_dirty_rect`` on the next ``paintGL`` call.  The
        CPU partial-recompose path has been removed (Phase 5b).
        """
        self._render_invalidation().recompose_region(dirty)

    def notify_vector_preview_changed(self) -> None:
        """Lightweight notification for vector preview updates.

        Bumps ``texture_version`` on the preview target layer so the GL
        pipeline re-rasterizes only that layer.  If no preview target is
        registered, bumps all vector layers as a conservative fallback.
        """
        self._render_invalidation().notify_vector_preview_changed()

    def _recompose(self) -> None:
        """Notify the GPU compositor that a full recompose is needed.

        Under GPU-unified rendering (Phase 3+) this is a lightweight
        signal: it sets ``_gpu_full_recompose`` so the next ``paintGL``
        reuploads dirty textures, then emits ``composited_changed``
        for observers that track the last composited pixmap.  Deferred
        batches (``defer_recompose``/``resume_recompose``) still apply.
        """
        self._render_invalidation().recompose()

    def _recompose_now(self) -> None:
        """Synchronous recompose alias — identical to ``_recompose`` in GPU mode."""
        self._render_invalidation().recompose_now()

    def _prune_qpath_cache(self, preview_obj: Any | None = None) -> None:
        """Remove QPainterPath cache entries for objects no longer in any layer."""
        self._vector_text_render().prune_qpath_cache(preview_obj)

    def _draw_vector_layer(
        self,
        painter: QPainter,
        layer: _Layer,
        selection: dict[str, Any] | None,
        preview_obj: Any | None,
        preview_layer_id: str | None,
    ) -> None:
        """Draw a vector layer including optional preview object."""
        self._vector_text_render().draw_vector_layer(
            painter,
            layer,
            selection,
            preview_obj,
            preview_layer_id,
        )

    def _rasterize_group_layer(self, layer: _Layer) -> QImage:
        """Composite a group's visible children onto a transparent
        canvas-sized QImage.

        Phase 6-d'-4: lets the AI Effect input-resolution path bind a
        group layer as a slot source without forcing the user to
        flatten the group manually. Mirrors ``_composite_visible``'s
        clipping/effect/blend handling by reusing that method on a
        child-only ``visible`` list. Cross-group references (a mask
        attached to the parent group, etc.) are NOT considered —
        only the children's own painting contributes. Acceptable
        first cut: dogfood scenarios so far have used flat groups
        without external dependencies.

        Empty / non-group layers return a transparent canvas image so
        the caller's null-check path stays simple.
        """
        return self._vector_text_render().rasterize_group_layer(layer)

    def _rasterize_vector_layer(
        self,
        layer: _Layer,
        scale: int = 1,
        region: tuple[int, int, int, int] | None = None,
    ) -> QImage:
        from .comic_panels.vector_page_basis import rasterize_in_page_basis  # noqa: PLC0415

        result = rasterize_in_page_basis(self, layer, scale=scale, region=region)
        if result is not None:
            return result
        return self._vector_text_render().rasterize_vector_layer(layer, scale=scale, region=region)

    def _rasterize_vector_layer_for_render(
        self,
        layer: _Layer,
        scale: int = 1,
    ) -> QImage | VectorRasterResult | None:
        from .comic_panels.vector_page_basis import rasterize_in_page_basis  # noqa: PLC0415

        result = rasterize_in_page_basis(self, layer, scale=scale, for_render=True)
        if result is not None:
            return result
        return self._vector_text_render().rasterize_vector_layer_for_render(layer, scale=scale)

    def _is_vector_preview_object(self, vec: Any) -> bool:
        return VectorTextRenderService.is_vector_preview_object(
            vec,
            getattr(self, "_vector_preview", None),
        )

    def _rasterize_vector_coverage_layer(
        self,
        layer: _Layer,
        scale: int = 1,
        region: tuple[int, int, int, int] | None = None,
    ) -> QImage:
        from .comic_panels.vector_page_basis import rasterize_in_page_basis  # noqa: PLC0415

        result = rasterize_in_page_basis(self, layer, scale=scale, region=region)
        if result is not None:
            return result
        return self._vector_text_render().rasterize_vector_coverage_layer(
            layer, scale=scale, region=region
        )

    def _rasterize_vector_coverage_layer_for_render(
        self, layer: _Layer, scale: int = 1
    ) -> QImage | VectorRasterResult | None:
        from .comic_panels.vector_page_basis import rasterize_in_page_basis  # noqa: PLC0415

        result = rasterize_in_page_basis(self, layer, scale=scale, for_render=True)
        if result is not None:
            return result
        return self._vector_text_render().rasterize_vector_coverage_layer_for_render(
            layer, scale=scale
        )

    def _should_draw_vector_path_controls(  # noqa: PLR0911
        self,
        *,
        vec: Any,
        layer_id: str,
        path_idx: int,
        selection: dict[str, Any] | None,
    ) -> bool:
        return VectorTextRenderService.should_draw_vector_path_controls(
            vec=vec,
            layer_id=layer_id,
            path_idx=path_idx,
            selection=selection,
            preview_obj=getattr(self, "_vector_preview", None),
        )

    def _is_parametric_shape_editable(self, vec: Any) -> bool:
        return VectorTextRenderService.is_parametric_shape_editable(vec)

    def _get_cached_qpaths(self, vec: Any) -> list[QPainterPath]:
        """Return cached QPainterPath list for a VectorObject, building if needed."""
        return self._vector_text_render().get_cached_qpaths(vec)

    @staticmethod
    def _build_vector_qpaths(vec: Any) -> list[QPainterPath]:
        """Build QPainterPath list from a VectorObject's paths."""
        return VectorTextRenderService.build_vector_qpaths(vec)

    def _draw_vector_objects(
        self,
        painter: QPainter,
        layer_id: str,
        objects: Sequence[Any],
        selection: dict[str, Any] | None,
    ) -> None:
        """Render vector objects with optional selection highlighting."""
        self._vector_text_render().draw_vector_objects(
            painter,
            layer_id,
            objects,
            selection,
        )

    def _compute_vector_bbox(self, vec: Any) -> tuple[float, float, float, float] | None:
        return VectorTextRenderService.vector_bbox(vec)

    def _compute_path_bbox(self, vec: Any) -> tuple[float, float, float, float] | None:
        return VectorTextRenderService.path_bbox(vec)

    @staticmethod
    def _coerce_vector_overlay_bounds(
        raw_bounds: Any,
        fallback: tuple[float, float, float, float] | None = None,
    ) -> tuple[float, float, float, float] | None:
        return VectorTextRenderService.coerce_overlay_bounds(raw_bounds, fallback)

    def _draw_vector_transform_overlay(
        self, painter: QPainter, bounds: tuple[float, float, float, float]
    ) -> None:
        VectorTextRenderService.draw_vector_transform_overlay(painter, bounds)

    def _draw_vector_anchor_box_overlay(self, painter: QPainter, raw_bounds: Any) -> None:
        VectorTextRenderService.draw_vector_anchor_box_overlay(painter, raw_bounds)

    def _draw_vector_shape_handles(self, painter: QPainter, handles: Any) -> None:
        VectorTextRenderService.draw_vector_shape_handles(painter, handles)

    def _draw_vector_anchor_marker(self, painter: QPainter, anchor: Any, selected: Any) -> None:
        VectorTextRenderService.draw_vector_anchor_marker(painter, anchor, selected)

    def _draw_selection_overlay(self, painter: QPainter) -> None:
        """Draw selection preview image only.

        Marching-ants border is drawn by CanvasWidget.paintEvent
        so it can animate without triggering a full recompose.
        """
        self._selection_state().draw_selection_overlay(painter)

    def _get_composition_mode(self, blend_mode: BlendMode) -> QPainter.CompositionMode:
        return CanvasBlendService.get_composition_mode(blend_mode)

    @staticmethod
    def _needs_custom_blend(blend_mode: BlendMode) -> bool:
        """Return True for blend modes that require pixel-level compositing."""
        return CanvasBlendService.needs_custom_blend(blend_mode)

    @staticmethod
    def _rgb_to_hsl(rgb: NDArray[Any]) -> NDArray[Any]:
        """Convert (h, w, 3) float32 [0,255] RGB to HSL [h:0-6, s:0-1, l:0-1]."""
        return CanvasBlendService.rgb_to_hsl(rgb)

    @staticmethod
    def _hsl_to_rgb(hsl: NDArray[Any]) -> NDArray[Any]:
        """Convert (h, w, 3) HSL [h:0-6, s:0-1, l:0-1] to float32 RGB [0,255]."""
        return CanvasBlendService.hsl_to_rgb(hsl)

    @staticmethod
    def _custom_blend(
        dst: QImage,
        src: QImage,
        blend_mode: BlendMode,
        opacity: float,
    ) -> None:
        """Composite *src* onto *dst* in-place using pixel math."""
        CanvasBlendService.custom_blend(dst, src, blend_mode, opacity)

    def attach_canvas_widget(self, widget: Any) -> None:
        """Register the CanvasWidget used for GPU readback.

        Called once during main-window setup. Subsequent
        ``get_composited_pixmap()`` calls flush the GPU compositor
        through this widget rather than returning the stale CPU
        cache.
        """
        self._pixmap_readback().attach_canvas_widget(widget)

    def get_composited_pixmap(self) -> QPixmap:
        """Return the composited canvas as a QPixmap.

        Phase 2 compatibility shim: previously returned the CPU
        composited ``self._composited``. Now performs a GPU flush +
        readback via the registered CanvasWidget and caches the
        result. Falls back to the last known ``self._composited``
        when no widget is attached or GPU readback fails (e.g.
        during early boot before ``initializeGL`` runs).
        """
        return self._pixmap_readback().get_composited_pixmap()

    def get_cpu_composited_pixmap(self) -> QPixmap:
        """Return a CPU-composited layer stack for diagnostic rendering.

        This intentionally bypasses the CanvasWidget GPU readback bridge and
        is used only by the opt-in CPU canvas experiment.
        """
        return self._cpu_composite().get_cpu_composited_pixmap()

    def get_canvas_size(self) -> tuple[int, int]:
        """Return the current canvas size as ``(width, height)``."""
        return self._runtime_snapshot().get_canvas_size()

    def rasterize_layer(self, layer_id: str) -> bool:
        """Convert a vector layer into a raster layer in-place."""
        return self._layer_rasterization().rasterize_layer(layer_id)
