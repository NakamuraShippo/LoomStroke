"""Release document-owned state while keeping the application's services alive."""

# ruff: noqa: SLF001 - core lifecycle collaborator owns private document state.

from __future__ import annotations

from contextlib import nullcontext
from typing import TYPE_CHECKING
from uuid import uuid4

from PySide6.QtGui import QColor

from .canvas import Canvas as LegacyCanvas
from .canvas_invalidation import GpuResourceInvalidation
from .canvas_service_simple import SimpleCanvasService

if TYPE_CHECKING:
    from .document_service import DocumentServiceImpl


class DocumentCloseService:
    """Restore the startup canvas and drop all state belonging to a closed file."""

    def __init__(self, owner: DocumentServiceImpl) -> None:
        self._owner = owner
        canvas = owner._canvas
        # Capture values, never layer/pixel references from the startup canvas.
        self._startup_canvas = (
            owner._metadata_service.extract_canvas_metadata()
            if isinstance(canvas, (SimpleCanvasService, LegacyCanvas))
            else None
        )
        self._startup_max_layers = getattr(canvas, "_max_layers", None)
        self._startup_tile_size = getattr(canvas, "_tile_size", None)

    def close_document(self) -> None:
        owner = self._owner
        canvas = owner._canvas
        if self._startup_canvas is None:
            raise RuntimeError("Unsupported canvas implementation")

        filter_pen = getattr(
            canvas, "_filter_pen_service", getattr(canvas, "_effect_pen_service", None)
        )
        if filter_pen is not None:
            # Rollback targets must still exist when cancellation invalidates
            # in-flight workers and releases their retained stroke session.
            cancel = getattr(filter_pen, "cancel", None)
            if callable(cancel):
                cancel()
            else:
                filter_pen.finalize()

        pages = owner._page_service
        batch = pages._history_state_notifier.batch() if pages is not None else nullcontext()
        with batch:
            if pages is not None:
                pages._bind_page_hydration_port(None)
                pages._set_pages([], active_page_id=None, apply_active=False, dirty=False)
                pages._document_canvas = None
                pages._page_access_counter = 0

            owner._document_state = None
            owner._loaded_metadata = None
            owner._metadata_service.adopt_loaded_color_profile(None)
            if isinstance(canvas, SimpleCanvasService):
                self._reset_simple_canvas(canvas)
            elif isinstance(canvas, LegacyCanvas):
                self._reset_legacy_canvas(canvas)
            owner._emit_history_reset("close")

    def _reset_simple_canvas(self, canvas: SimpleCanvasService) -> None:
        startup = self._startup_canvas
        assert startup is not None
        canvas._mask_commit_timer.stop()
        canvas._mask_paint_dirty = False
        canvas._mask_paint_layer_id = None
        canvas._mask_paint_mask_snapshot = None
        canvas._stroke_painting_active = False
        canvas._defer_recompose_depth = 0
        canvas._defer_recompose_needed = False
        canvas._qpath_cache.clear()
        canvas._vector_raster_cache.clear()
        tombstones = getattr(canvas, "_ai_effect_output_tombstones", None)
        if tombstones is not None:
            tombstones.clear()
        references = getattr(canvas, "_reference_registry", None)
        if references is not None:
            references.clear()
        canvas._prev_composited = None
        canvas._effect_cache_bytes = 0
        canvas._render_invalidation_service._story_preview_layers.clear()
        if self._startup_max_layers is not None:
            canvas._max_layers = self._startup_max_layers
        if self._startup_tile_size is not None:
            canvas._tile_size = self._startup_tile_size
        # Fresh default layers reuse the existing lifecycle implementation;
        # suppress document edits while restoring the no-document workspace.
        with canvas.suspend_doc_events():
            canvas.reset_document(
                width=startup.width,
                height=startup.height,
                dpi=int(startup.dpi[0]),
                background=QColor(*startup.background_color),
            )
        canvas._gpu_resource_invalidation = GpuResourceInvalidation.for_all(reason="document_close")
        canvas._emit_selection_changed()

    def _reset_legacy_canvas(self, canvas: LegacyCanvas) -> None:
        startup = self._startup_canvas
        assert startup is not None
        canvas.config.width = startup.width
        canvas.config.height = startup.height
        canvas.config.background_color = startup.background_color
        canvas.layers.clear()
        canvas._active_index = -1
        canvas._document_id = f"doc-{uuid4().hex[:8]}"
        canvas._transaction_depth = 0
        canvas._selection_mask = None
        canvas._selection_bounds = None
        canvas._selection_mask_full = None
        canvas._composite_cache = None
        canvas.history_clear()
        canvas.add_layer("Background", make_active=False, fill_color=startup.background_color)
        canvas.add_layer("Layer 1", make_active=True)
        canvas.clear_dirty()
