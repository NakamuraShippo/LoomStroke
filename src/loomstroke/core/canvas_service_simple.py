"""Simple canvas service implementation with layer support.

This provides a minimal but functional canvas service that:
- Manages multiple layers with visibility and opacity
- Handles paint commands through the Command pattern
- Provides composited output for UI display
- Follows Master.md principles for separation of concerns
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Any, TypeAlias

from numpy.typing import NDArray
from PySide6.QtCore import QObject, QRect, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainterPath,
    QPixmap,
)

from loomstroke.core.canvas_ai_effect_state_service import CanvasAIEffectStateService
from loomstroke.core.canvas_alpha_preservation_service import CanvasAlphaPreservationService
from loomstroke.core.canvas_apply_image_service import CanvasApplyImageService
from loomstroke.core.canvas_blend_service import CanvasBlendService
from loomstroke.core.canvas_command_dirty_region_service import CanvasCommandDirtyRegionService
from loomstroke.core.canvas_command_routing_service import CanvasCommandRoutingService
from loomstroke.core.canvas_composited_pixmap_state_service import (
    CanvasCompositedPixmapStateService,
)
from loomstroke.core.canvas_cpu_composite_service import CanvasCpuCompositeService
from loomstroke.core.canvas_document_event_service import CanvasDocumentEventService
from loomstroke.core.canvas_fill_gradient_submit_service import CanvasFillGradientSubmitService
from loomstroke.core.canvas_geometry_service import CanvasGeometryService
from loomstroke.core.canvas_history_adapter import (
    CanvasCommandDispatcher,
    CanvasHistoryService,
    HistoryAdapter,
)
from loomstroke.core.canvas_history_entries import (
    HistoryEntry,
    _compress_qimage,
    _decompress_qimage,
    _FlattenAllEntry,
    _HistoryCommand,
    _HistoryGroup,
    _HistoryPatch,
    _MergeEntry,
    _MergeVisibleEntry,
    _ResizeEntry,
    _ResizeSnapshot,
)
from loomstroke.core.canvas_invalidation import (
    GpuResourceInvalidation,
)
from loomstroke.core.canvas_layer_bounds_service import CanvasLayerBoundsService
from loomstroke.core.canvas_layer_content_bounds_service import CanvasLayerContentBoundsService
from loomstroke.core.canvas_layer_content_mutation_service import (
    CanvasLayerContentMutationService,
)
from loomstroke.core.canvas_layer_event_service import CanvasLayerEventService
from loomstroke.core.canvas_layer_image_service import CanvasLayerImageService
from loomstroke.core.canvas_layer_mask_service import CanvasLayerMaskService
from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.canvas_layer_property_service import CanvasLayerPropertyService
from loomstroke.core.canvas_layer_query_service import CanvasLayerQueryService
from loomstroke.core.canvas_layer_rasterization_service import (
    CanvasLayerRasterizationService,
)
from loomstroke.core.canvas_layer_snapshot_service import CanvasLayerSnapshotService
from loomstroke.core.canvas_layer_transform_preview_service import (
    CanvasLayerTransformPreviewService,
)
from loomstroke.core.canvas_layer_visibility_submit_service import (
    CanvasLayerVisibilitySubmitService,
)
from loomstroke.core.canvas_mask_layer_service import CanvasMaskLayerService
from loomstroke.core.canvas_mask_stroke_commit_service import CanvasMaskStrokeCommitService
from loomstroke.core.canvas_mask_stroke_service import CanvasMaskStrokeService
from loomstroke.core.canvas_merge_flatten_service import CanvasMergeFlattenService
from loomstroke.core.canvas_models import (
    _is_text_vector_object,
    _MaskShapeContour,
)
from loomstroke.core.canvas_pixmap_readback_service import CanvasPixmapReadbackService
from loomstroke.core.canvas_raster_preimage_capture_service import (
    CanvasRasterPreimageCaptureService,
)
from loomstroke.core.canvas_render_invalidation_service import (
    CanvasRenderInvalidationService,
)
from loomstroke.core.canvas_runtime_snapshot_service import CanvasRuntimeSnapshotService
from loomstroke.core.canvas_selection_change_submit_service import (
    CanvasSelectionChangeSubmitService,
)
from loomstroke.core.canvas_selection_content_submit_service import (
    CanvasSelectionContentSubmitService,
)
from loomstroke.core.canvas_selection_history_service import CanvasSelectionHistoryService
from loomstroke.core.canvas_selection_mask_submit_service import CanvasSelectionMaskSubmitService
from loomstroke.core.canvas_service_simple_accessors import CanvasServiceAccessorsMixin
from loomstroke.core.canvas_service_simple_channels_api import CanvasServiceChannelsApiMixin
from loomstroke.core.canvas_service_simple_command_api import CanvasServiceCommandApiMixin
from loomstroke.core.canvas_service_simple_filter_api import CanvasServiceFilterApiMixin
from loomstroke.core.canvas_service_simple_history_api import CanvasServiceHistoryApiMixin
from loomstroke.core.canvas_service_simple_image_api import CanvasServiceImageApiMixin
from loomstroke.core.canvas_service_simple_layer_api import CanvasServiceLayerApiMixin
from loomstroke.core.canvas_service_simple_render_api import CanvasServiceRenderApiMixin
from loomstroke.core.canvas_service_simple_stroke_api import CanvasServiceStrokeApiMixin
from loomstroke.core.canvas_service_simple_text_api import CanvasServiceTextApiMixin
from loomstroke.core.canvas_simple_layer_command_submit_service import (
    CanvasSimpleLayerCommandSubmitService,
)
from loomstroke.core.canvas_stroke_application_service import CanvasStrokeApplicationService
from loomstroke.core.canvas_stroke_draw_primitive_service import (
    CanvasStrokeDrawPrimitiveService,
)
from loomstroke.core.canvas_stroke_layer_paint_service import CanvasStrokeLayerPaintService
from loomstroke.core.canvas_stroke_pixel_commit_service import CanvasStrokePixelCommitService
from loomstroke.core.canvas_stroke_stamp_brush_service import CanvasStrokeStampBrushService
from loomstroke.core.canvas_stroke_submit_service import CanvasStrokeSubmitService
from loomstroke.core.canvas_stroke_target_service import CanvasStrokeTargetService
from loomstroke.core.canvas_transform_image_service import CanvasTransformImageService
from loomstroke.core.canvas_transform_submit_service import CanvasTransformSubmitService
from loomstroke.core.canvas_tree_command_submit_service import CanvasTreeCommandSubmitService
from loomstroke.core.canvas_vector_command_submit_service import (
    CanvasVectorCommandSubmitService,
)
from loomstroke.core.commands import (
    SELECTION_MASK_LAYER_ID,
    BlendMode,
    LayerTransform,
    LayerVisibilityCommand,
)
from loomstroke.core.document_capture_guard import DocumentCaptureGuardMixin
from loomstroke.core.fill.commands_fill import (
    FloodFillCommand,
    FloodFillToNewLayerCommand,
    PatternFillCommand,
)
from loomstroke.core.filters import FilterRegistry
from loomstroke.core.layer_tree_service import LayerTreeService
from loomstroke.core.selection_state import (
    SelectionStateService,
)
from loomstroke.core.vector_text_render_service import (
    VectorTextRenderService,
)
from loomstroke.host_api import (
    DocumentRef,
    EventBus,
    LayerRef,
)

from .events import DocumentChangeEvent, Events, HistoryChangedEvent

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)
_PEN_LAG_DIAG = os.environ.get("LOOMSTROKE_PEN_LAG_DIAG", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


__all__ = (
    "HistoryEntry",
    "SimpleCanvasService",
    "_FlattenAllEntry",
    "_HistoryCommand",
    "_HistoryGroup",
    "_HistoryPatch",
    "_Layer",
    "_MaskShapeContour",
    "_MergeEntry",
    "_MergeVisibleEntry",
    "_ResizeEntry",
    "_ResizeSnapshot",
    "_compress_qimage",
    "_decompress_qimage",
)


LayerValue: TypeAlias = LayerRef | Mapping[str, int] | bool | str | int | None


class SimpleCanvasService(
    DocumentCaptureGuardMixin,
    CanvasServiceChannelsApiMixin,
    CanvasServiceLayerApiMixin,
    CanvasServiceCommandApiMixin,
    CanvasServiceStrokeApiMixin,
    CanvasServiceImageApiMixin,
    CanvasServiceHistoryApiMixin,
    CanvasServiceFilterApiMixin,
    CanvasServiceRenderApiMixin,
    CanvasServiceTextApiMixin,
    CanvasServiceAccessorsMixin,
    QObject,
):
    """Simple canvas service with layer management.

    This implementation provides:
    - Multiple layers with visibility/opacity control
    - Paint command execution on active layer
    - Automatic recomposition on changes
    - Signal emission for UI updates
    """

    # Signal emitted when composite changes
    composited_changed = Signal(QPixmap)

    def __init__(  # noqa: PLR0915
        self,
        width: int = 1920,
        height: int = 1080,
        background: QColor | None = None,
        dpi: int = 96,
        tile_size: int = 256,
        max_layers: int = 100,
        *,
        max_history_steps: int = 50,
    ) -> None:
        """Initialize canvas service.

        Args:
            width: Canvas width in pixels
            height: Canvas height in pixels
            background: Background color
            dpi: Document DPI used for metadata.
            tile_size: Tile size hint used for caching/backing storage.
            max_layers: Maximum allowed layers for the document
        """
        super().__init__()

        # Document info
        self._doc = DocumentRef(id="doc-0", name="Untitled")

        # Layer management
        self._layers: list[_Layer] = []
        self._active_index = 0
        self._active_layer_id: str | None = None

        # Event bus reference (set via set_event_bus)
        self._event_bus: EventBus | None = None
        self._suppress_doc_events = 0

        # Canvas dimensions
        self._width = int(width)
        self._height = int(height)
        default_background = QColor(255, 255, 255) if background is None else background
        self._background = default_background
        self._dpi = max(1, int(dpi))
        self._tile_size = max(1, int(tile_size))
        self._max_layers = max(1, int(max_layers))
        # Document working ICC profile (``io.file_io.ColorProfile`` or None =
        # untagged / treated as sRGB).  Adopted on load, saved by the document
        # metadata service, embedded by export (CT-09).  Pixels never change.
        self.color_profile: Any = None

        # Composited result
        self._composited = CanvasCompositedPixmapStateService.make_filled_pixmap(
            width,
            height,
            self._background,
        )
        self._prev_composited: QPixmap | None = None  # GC guard for GL texture

        # GPU readback bridge (Phase 2): CanvasWidget reference used by
        # get_composited_pixmap() to flush + readback the GPU compositor.
        self._canvas_widget: Any = None

        # GPU rendering is mandatory (Phase 3+). _recompose() is a
        # lightweight notification that sets _gpu_full_recompose and
        # emits composited_changed; no CPU throttling is required.
        self._defer_recompose_depth = 0
        self._defer_recompose_needed = False

        # Mask stroke preview (deferred commit)
        self._mask_paint_dirty = False
        self._mask_commit_timer = QTimer(self)
        self._mask_commit_timer.setSingleShot(True)
        self._mask_commit_timer.setInterval(150)
        self._mask_commit_timer.timeout.connect(self._commit_mask_paint)
        # Snapshot of layer_mask before mask painting starts (effect layers only).
        # Used during recompose so intermediate strokes don't trigger effect
        # recomputation until the stroke is committed.
        self._mask_paint_layer_id: str | None = None
        self._mask_paint_mask_snapshot: QImage | None = None
        # Layer IDs whose mask texture must be re-uploaded by the GPU
        # pipeline on the next frame (set during mask-edit strokes).
        self._gpu_mask_dirty_layers: set[str] = set()

        # Filter cache management
        self._filter_cache_bytes: int = 0
        self._recompose_count: int = 0

        # Composite version counter. Bumped by _invalidate_layer_cache() and
        # consumed by external observers (e.g. layer_panel thumbnail cache)
        # as a signal that the layer tree has changed.
        self._layer_cache_version: int = 0

        # Transaction state.  Deferred-paint preimages are owned by the
        # capture service (first-touch fragments for raster strokes, an
        # explicit dense snapshot for mask edits — RJS P0-05).
        self._in_transaction = False
        self._transaction_label = ""
        self._txn_preimage_capture = CanvasRasterPreimageCaptureService()
        self._mask_edit_flag: bool = False
        # True while a paint stroke is active inside a transaction.
        # Used to skip expensive effect recalculation during strokes;
        # effects are fully applied on end_transaction → _recompose.
        self._stroke_painting_active: bool = False
        self._pen_lag_diag_count: int = 0
        if _PEN_LAG_DIAG:
            logger.warning("LOOMSTROKE_PEN_LAG_DIAG is enabled; sampling canvas service timings")
        self._history_limit = max(1, int(max_history_steps))
        self._undo_stack: deque[_HistoryGroup] = deque(maxlen=self._history_limit)
        self._redo_stack: deque[_HistoryGroup] = deque()
        self._active_history_group: _HistoryGroup | None = None
        self._command_dispatcher = CanvasCommandDispatcher(self)
        self._history_service = CanvasHistoryService(
            self,
            history_group_type=_HistoryGroup,
            history_command_type=_HistoryCommand,
            history_patch_type=_HistoryPatch,
            resize_entry_type=_ResizeEntry,
            merge_entry_type=_MergeEntry,
            merge_visible_entry_type=_MergeVisibleEntry,
            flatten_all_entry_type=_FlattenAllEntry,
            layer_visibility_command_type=LayerVisibilityCommand,
            flood_fill_alpha_command_types=(FloodFillCommand, PatternFillCommand),
            flood_fill_refresh_command_types=(
                FloodFillCommand,
                FloodFillToNewLayerCommand,
                PatternFillCommand,
            ),
            pattern_fill_command_type=PatternFillCommand,
            history_changed_event_type=HistoryChangedEvent,
            events=Events,
        )
        self._history_adapter = HistoryAdapter(self)
        self._vector_preview_layer_id: str | None = None
        self._selection_mask: QImage | None = None
        self._selection_bounds: QRect | None = None
        self._selection_id: str = SELECTION_MASK_LAYER_ID
        self._selection_mask_full: NDArray[Any] | None = None
        self._selection_preview_image: QImage | None = None
        self._selection_preview_bounds: tuple[int, int, int, int] | None = None
        self._selection_state_service = SelectionStateService(self)

        # GPU rendering flag (when True, _recompose short-circuits)
        self._gpu_rendering: bool = False
        # Accumulated dirty rect for GPU texture sub-upload
        self._gpu_dirty_rect: QRect | None = None
        # Full recompose needed (layer add/remove/reorder, undo, etc.)
        self._gpu_full_recompose: bool = False
        self._gpu_resource_invalidation: GpuResourceInvalidation = GpuResourceInvalidation.empty()

        # QPainterPath cache for vector objects (keyed by Python object id)
        self._qpath_cache: dict[int, list[QPainterPath]] = {}
        # (version, prescale, canvas_w, canvas_h, (origin_x, origin_y), image)
        self._vector_raster_cache: dict[str, tuple[int, int, int, int, tuple[int, int], QImage]] = (
            {}
        )
        self._vector_text_render_service = VectorTextRenderService(self)
        self._layer_tree_service = LayerTreeService(
            self,
            layer_type=_Layer,
            document_change_event_type=DocumentChangeEvent,
            blend_mode_type=BlendMode,
            normal_blend_mode=BlendMode.NORMAL,
            identity_transform=LayerTransform.identity,
        )
        self._cpu_composite_service = CanvasCpuCompositeService(self)
        self._ai_effect_state_service = CanvasAIEffectStateService(
            self,
            layer_type=_Layer,
            events=Events,
        )
        self._filter_apply_service = CanvasLayerImageService(
            self,
            filter_registry=FilterRegistry,
            layer_type=_Layer,
            events=Events,
        )
        self._geometry_service = CanvasGeometryService(
            self,
            resize_snapshot_type=_ResizeSnapshot,
            resize_entry_type=_ResizeEntry,
            history_group_type=_HistoryGroup,
            compress_qimage=_compress_qimage,
            decompress_qimage=_decompress_qimage,
        )
        self._layer_snapshot_service = CanvasLayerSnapshotService(self)
        self._alpha_preservation_service = CanvasAlphaPreservationService(self)
        self._apply_image_service = CanvasApplyImageService(self)
        self._command_dirty_region_service = CanvasCommandDirtyRegionService(self)
        self._command_routing_service = CanvasCommandRoutingService(self)
        self._layer_bounds_service = CanvasLayerBoundsService(self)
        self._layer_content_bounds_service = CanvasLayerContentBoundsService(self)
        self._layer_content_mutation_service = CanvasLayerContentMutationService(self)
        self._document_event_service = CanvasDocumentEventService(self)
        self._layer_event_service = CanvasLayerEventService(self)
        self._layer_query_service = CanvasLayerQueryService(self)
        self._layer_mask_service = CanvasLayerMaskService(self)
        self._layer_property_service = CanvasLayerPropertyService(self)
        self._layer_rasterization_service = CanvasLayerRasterizationService(self)
        self._layer_transform_preview_service = CanvasLayerTransformPreviewService(self)
        self._mask_layer_service = CanvasMaskLayerService(self)
        self._mask_stroke_commit_service = CanvasMaskStrokeCommitService(self)
        self._mask_stroke_service = CanvasMaskStrokeService(self)
        self._merge_flatten_service = CanvasMergeFlattenService(
            self,
            layer_type=_Layer,
            merge_entry_type=_MergeEntry,
            merge_visible_entry_type=_MergeVisibleEntry,
            flatten_all_entry_type=_FlattenAllEntry,
            history_group_type=_HistoryGroup,
            normal_blend_mode=BlendMode.NORMAL,
            compress_qimage=_compress_qimage,
            decompress_qimage=_decompress_qimage,
            events=Events,
        )
        self._pixmap_readback_service = CanvasPixmapReadbackService(self)
        self._render_invalidation_service = CanvasRenderInvalidationService(self)
        self._runtime_snapshot_service = CanvasRuntimeSnapshotService(self)
        self._selection_change_submit_service = CanvasSelectionChangeSubmitService(self)
        self._selection_content_submit_service = CanvasSelectionContentSubmitService(self)
        self._selection_history_service = CanvasSelectionHistoryService(
            self,
            patch_factory=_HistoryPatch.from_images,
            content_layer_type=_Layer,
        )
        self._selection_mask_submit_service = CanvasSelectionMaskSubmitService(self)
        self._layer_visibility_submit_service = CanvasLayerVisibilitySubmitService(
            self,
            layer_changed_event=Events.LAYER_CHANGED,
        )
        self._simple_layer_command_submit_service = CanvasSimpleLayerCommandSubmitService(self)
        self._stroke_application_service = CanvasStrokeApplicationService(self)
        self._stroke_draw_primitive_service = CanvasStrokeDrawPrimitiveService()
        self._stroke_layer_paint_service = CanvasStrokeLayerPaintService(self)
        self._stroke_pixel_commit_service = CanvasStrokePixelCommitService(self)
        self._stroke_stamp_brush_service = CanvasStrokeStampBrushService(self)
        self._stroke_submit_service = CanvasStrokeSubmitService(self)
        self._stroke_target_service = CanvasStrokeTargetService(self)
        self._transform_image_service = CanvasTransformImageService()
        self._transform_submit_service = CanvasTransformSubmitService(self)
        self._tree_command_submit_service = CanvasTreeCommandSubmitService(self)
        self._vector_command_submit_service = CanvasVectorCommandSubmitService(
            self,
            doc_changed_event=Events.DOC_CHANGED,
        )
        self._fill_gradient_submit_service = CanvasFillGradientSubmitService(
            self,
            flood_fill_to_new_layer_type=FloodFillToNewLayerCommand,
            pattern_fill_type=PatternFillCommand,
        )

        # Create default layers
        # Add background layer first (will be at index 0)
        self.add_layer("Background")
        # Then add initial drawing layer (will be at index 1)
        self.add_layer("Layer 1")
        # Set the drawing layer as active (not background)
        self._active_index = 1

        logger.info(f"SimpleCanvasService initialized: {width}x{height}")

    # --- CanvasService Interface ---

    def set_event_bus(self, event_bus: EventBus | None) -> None:
        """Attach an event bus used for publishing layer updates."""
        self._event_bus = event_bus

    def _emit_selection_changed(self) -> None:
        """Notify listeners that the selection mask has changed."""
        self._selection_state().emit_selection_changed()

    @contextmanager
    def suspend_doc_events(self) -> Iterator[None]:
        """Temporarily suppress DOC_CHANGED emissions (e.g., page switching)."""
        self._suppress_doc_events += 1
        try:
            yield
        finally:
            self._suppress_doc_events = max(0, self._suppress_doc_events - 1)

    def _emit_doc_changed(self, payload: DocumentChangeEvent) -> None:
        self._document_events().emit_doc_changed(payload)

    def reset_document(
        self,
        *,
        width: int,
        height: int,
        dpi: int | None = None,
        background: QColor | None = None,
        name: str | None = None,
    ) -> DocumentRef:
        """Reset the canvas to a fresh document state."""

        self._doc = DocumentRef(id=f"doc-{time.time_ns()}", name=name or "Untitled")
        self._width = max(1, int(width))
        self._height = max(1, int(height))
        if dpi is not None:
            self._dpi = max(1, int(dpi))
        if background is not None:
            self._background = background
        self.color_profile = None

        self._layers.clear()
        self._active_index = 0
        # Drop the previous document's active id with its layers; leaving it
        # dangling lets it resolve again if a layer with that id reappears.
        self._active_layer_id = None

        self._selection_state().clear_selection_state()
        self._gpu_dirty_rect = None
        self._gpu_full_recompose = False
        self._gpu_mask_dirty_layers.clear()
        self._gpu_resource_invalidation = GpuResourceInvalidation.empty()

        self._vector_preview = None
        self._vector_preview_layer_id = None
        self._vector_selection = None

        self._in_transaction = False
        self._transaction_label = ""
        self._active_history_group = None
        self._txn_preimage_capture.reset()
        self._mask_edit_flag = False

        self._composited = CanvasCompositedPixmapStateService.make_filled_pixmap(
            self._width,
            self._height,
            self._background,
        )

        self.add_layer("Background")
        self.add_layer("Layer 1")
        self._active_index = 1 if len(self._layers) > 1 else 0

        self.history_clear()
        self._recompose()

        payload = DocumentChangeEvent(
            change_type="new_document",
            affected_area=(0, 0, self._width, self._height),
            layer_id=self._layers[self._active_index].id if self._layers else None,
            source="canvas",
            timestamp=time.time(),
        )
        self._emit_doc_changed(payload)

        return self._doc

    # ------------------------------------------------------------------
    # Tree traversal helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Group layer operations
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Clipping mask operations
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Layer-attached mask operations
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Deferred paint history (transaction optimisation)
    # ------------------------------------------------------------------

    # --- Layer Management API ---

    # ------------------------------------------------------------------
    # Merge Visible Layers  (Phase A Wave 2 #3)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Flatten All Layers  (Phase A Wave 3 #2)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Canvas-level vector transforms (flip / rotate) — Phase A Wave 1 fix
    # ------------------------------------------------------------------

    # --- Event helpers ---

    @staticmethod
    def _is_text_vector_layer(layer: _Layer) -> bool:
        if layer.layer_type != "vector":
            return False
        vector_kind = str(getattr(layer, "vector_kind", "draw") or "draw").strip().lower()
        if vector_kind == "text":
            return True
        name = str(getattr(layer, "name", "") or "").strip().lower()
        if name.startswith("text layer"):
            return True
        return any(
            _is_text_vector_object(obj) for obj in getattr(layer, "vector_objects", []) or []
        )

    @classmethod
    def _is_draw_vector_layer(cls, layer: _Layer) -> bool:
        vector_kind = str(getattr(layer, "vector_kind", "draw") or "draw").strip().lower()
        return (
            layer.layer_type == "vector"
            and vector_kind == "draw"
            and not cls._is_text_vector_layer(layer)
        )

    def ensure_vector_layer(self) -> LayerRef | None:
        """Ensure an active draw-vector layer exists and return its reference."""
        logger.info("ensure_vector_layer called", stack_info=False)
        if not self._layers:
            return None
        # By id: a nested active vector layer must be recognised as such
        # instead of reporting the group that contains it.
        active = self._get_active_layer()
        if active is not None and self._is_draw_vector_layer(active):
            return LayerRef(
                id=active.id,
                name=active.name,
                layer_type="vector",
                vector_kind=active.vector_kind,
            )

        for layer in reversed(self._layers):
            if self._is_draw_vector_layer(layer):
                self.set_active_layer(layer.id)
                return LayerRef(
                    id=layer.id,
                    name=layer.name,
                    layer_type="vector",
                    vector_kind=layer.vector_kind,
                )

        return self.add_vector_layer()

    def set_vector_kind(
        self,
        layer_id: str,
        vector_kind: str,
        *,
        name: str | None = None,
    ) -> bool:
        """Change the vector_kind of a vector layer (e.g. shape → draw)."""
        layer = self._get_layer_by_id(layer_id)
        if layer is None or layer.layer_type != "vector":
            return False
        layer.vector_kind = vector_kind
        if name is not None and name.strip():
            layer.name = name.strip()
        self._publish_layer_event(
            Events.LAYER_CHANGED,
            layer=layer,
            change_type="property_changed",
        )
        return True

    def add_vector_object(
        self,
        vector_object: Any,
        *,
        layer_id: str | None = None,
        recompose: bool = False,
    ) -> tuple[str, int] | None:
        """Append a vector object to a vector layer."""

        target = self._get_layer_by_id(layer_id) if layer_id else None
        if target is None and self._layers:
            # Active layer by id, not the root entry at _active_index.
            target = self._get_active_layer()
        vector_object_is_text = _is_text_vector_object(vector_object)
        target_is_text_vector_layer = (
            target is not None
            and target.layer_type == "vector"
            and self._is_text_vector_layer(target)
        )
        if (
            target is None
            or target.layer_type != "vector"
            or (not vector_object_is_text and target_is_text_vector_layer)
            or (
                layer_id is None
                and not vector_object_is_text
                and not self._is_draw_vector_layer(target)
            )
        ):
            target_ref = self.ensure_vector_layer()
            target = self._get_layer_by_id(target_ref.id) if target_ref else None
        if target is None:
            logger.warning("No vector layer available for vector object")
            return None

        target.vector_objects.append(vector_object)
        index = len(target.vector_objects) - 1
        from .japanese_text.story_geometry import refresh_story_geometry  # noqa: PLC0415

        refresh_story_geometry(self, [vector_object])
        if isinstance(getattr(vector_object, "meta", None), dict):
            with suppress(Exception):
                vector_object.meta.setdefault("layer_id", target.id)
        if recompose:
            self._recompose()
        return target.id, index

    def replace_vector_object(
        self,
        target_id: str,
        new_object: Any,
        *,
        layer_id: str | None = None,
        recompose: bool = False,
    ) -> tuple[str, int, Any] | None:
        """Replace a vector object and return metadata for reverting."""

        layers = [self._get_layer_by_id(layer_id)] if layer_id else self._layers
        for layer in layers:
            if layer is None or layer.layer_type != "vector":
                continue
            for idx, obj in enumerate(layer.vector_objects):
                if getattr(obj, "id", None) == target_id:
                    previous = layer.vector_objects[idx]
                    self._qpath_cache.pop(id(previous), None)
                    layer.vector_objects[idx] = new_object
                    from .japanese_text.story_geometry import (  # noqa: PLC0415
                        refresh_story_geometry,
                    )

                    refresh_story_geometry(self, [previous, new_object])
                    if isinstance(getattr(new_object, "meta", None), dict):
                        with suppress(Exception):
                            new_object.meta.setdefault("layer_id", layer.id)
                    if recompose:
                        self._recompose()
                    return layer.id, idx, previous
        return None

    def remove_vector_object(
        self,
        layer_id: str,
        index: int,
        *,
        recompose: bool = False,
        vec_id: str | None = None,
    ) -> bool:
        """Remove a vector object by layer and index.

        When *vec_id* is supplied the method first verifies that the object at
        *index* has a matching ``id`` attribute.  If not, it falls back to a
        linear search by *vec_id* so that undo/redo remains correct even when
        indices have shifted due to other operations.
        """

        layer = self._get_layer_by_id(layer_id)
        if layer is None or layer.layer_type != "vector":
            return False

        target_index: int | None = None
        if vec_id is not None:
            # Prefer index if it still points at the right object
            if (
                0 <= index < len(layer.vector_objects)
                and getattr(layer.vector_objects[index], "id", None) == vec_id
            ):
                target_index = index
            # Fallback: search by ID
            if target_index is None:
                for i, obj in enumerate(layer.vector_objects):
                    if getattr(obj, "id", None) == vec_id:
                        target_index = i
                        break
        elif 0 <= index < len(layer.vector_objects):
            target_index = index

        if target_index is not None:
            removed = layer.vector_objects[target_index]
            self._qpath_cache.pop(id(layer.vector_objects[target_index]), None)
            del layer.vector_objects[target_index]
            from .japanese_text.story_geometry import refresh_story_geometry  # noqa: PLC0415

            refresh_story_geometry(self, [removed])
            if recompose:
                self._recompose()
            return True
        return False

    def get_vector_objects(self, *, include_hidden: bool = False) -> list[tuple[str, Any]]:
        """Return (layer_id, vector_object) pairs for vector layers.

        Args:
            include_hidden: When True, include objects from hidden layers.
                This is needed for undo/cleanup operations that must see all
                objects regardless of visibility.
        """

        return self._layer_query().get_vector_objects(include_hidden=include_hidden)

    def _collect_vector_objects(
        self,
        layers: list[_Layer],
        result: list[tuple[str, Any]],
        include_hidden: bool,
    ) -> None:
        """Recursively collect vector objects from all layers including groups."""
        self._layer_query()._collect_vector_objects(layers, result, include_hidden)

    # --- Internal Methods ---

    # --- History helpers -------------------------------------------------

    # ------------------------------------------------------------------
    # Filter cache management (D-2: unified)
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Filter layer API
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # AI Filter layer API (Phase 2-6-1)
    # ------------------------------------------------------------------
    # Fills the gap left by Phase 2 — ``_Layer.ai_effect`` was added in
    # Phase 2-2 and ``GenerateAIEffectCommand`` was wired to a Protocol in
    # Phase 2-4, but SimpleCanvasService never implemented the Protocol.
    # These three methods satisfy ``GenerateAIEffectCanvas`` (see
    # ``plugins/ai_effect/commands.py``) for add/set_state/set_image. The
    # remaining Protocol members (remove_layers / add_ai_effect_output_layers)
    # land in Phase 2-6-2. Registry cascade on delete lands in Phase 2-6-3.

    # ------------------------------------------------------------------
    # AI Effect reference registry — wiring helpers (Phase 2-6-3)
    # ------------------------------------------------------------------
    # Bridges ``LayerReferenceRegistry`` (Phase 2-3) into the canvas
    # service. The registry tracks which AI Effect Layer slot_refs
    # point at which source layers so (1) delete cascades can null out
    # stale slot_refs and (2) ref-count badges have a single source of
    # truth. The registry itself stays in the plugins package; this
    # service imports it lazily to avoid pulling the whole ai_effect
    # package at core import time.

    # --- Layer composition version ---

    # Blend modes that can use QPainter's native SourceOver path.
    # All other modes use _custom_blend for GPU-matching results.
    _QPAINTER_BLEND_MODES = CanvasBlendService.QPAINTER_BLEND_MODES

    # ------------------------------------------------------------------
    # Mask layer operations
    # ------------------------------------------------------------------


# Keep the definition-time callable so later class overrides stay custom.
_DEFAULT_ADD_VECTOR_OBJECT = SimpleCanvasService.add_vector_object
