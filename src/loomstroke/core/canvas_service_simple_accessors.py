"""Lazy service accessors for ``SimpleCanvasService``.

This mixin owns no state.  It only recreates service collaborators when
older tests or deserialization paths construct a canvas with missing
private service attributes.
"""

from __future__ import annotations

from loomstroke.core.canvas_ai_effect_state_service import CanvasAIEffectStateService
from loomstroke.core.canvas_alpha_preservation_service import CanvasAlphaPreservationService
from loomstroke.core.canvas_apply_image_service import CanvasApplyImageService
from loomstroke.core.canvas_command_dirty_region_service import CanvasCommandDirtyRegionService
from loomstroke.core.canvas_command_routing_service import CanvasCommandRoutingService
from loomstroke.core.canvas_cpu_composite_service import CanvasCpuCompositeService
from loomstroke.core.canvas_document_event_service import CanvasDocumentEventService
from loomstroke.core.canvas_fill_gradient_submit_service import CanvasFillGradientSubmitService
from loomstroke.core.canvas_geometry_service import CanvasGeometryService
from loomstroke.core.canvas_history_adapter import CanvasHistoryService
from loomstroke.core.canvas_history_entries import (
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
from loomstroke.core.canvas_history_port import CanvasHistoryPortService
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
from loomstroke.core.canvas_pixmap_readback_service import CanvasPixmapReadbackService
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
from loomstroke.core.commands import BlendMode, LayerTransform, LayerVisibilityCommand
from loomstroke.core.events import DocumentChangeEvent, Events, HistoryChangedEvent
from loomstroke.core.fill.commands_fill import (
    FloodFillCommand,
    FloodFillToNewLayerCommand,
    PatternFillCommand,
)
from loomstroke.core.filters import FilterRegistry
from loomstroke.core.image_size.canvas_resample_service import CanvasResampleService
from loomstroke.core.layer_tree_service import LayerTreeService
from loomstroke.core.selection_state import SelectionStateService
from loomstroke.core.vector_text_render_service import VectorTextRenderService


class CanvasServiceAccessorsMixin:
    def _selection_state(self) -> SelectionStateService:
        service = getattr(self, "_selection_state_service", None)
        if not isinstance(service, SelectionStateService):
            service = SelectionStateService(self)
            self._selection_state_service = service
        return service

    def _layer_tree(self) -> LayerTreeService:
        service = getattr(self, "_layer_tree_service", None)
        if not isinstance(service, LayerTreeService):
            service = LayerTreeService(
                self,
                layer_type=_Layer,
                document_change_event_type=DocumentChangeEvent,
                blend_mode_type=BlendMode,
                normal_blend_mode=BlendMode.NORMAL,
                identity_transform=LayerTransform.identity,
            )
            self._layer_tree_service = service
        return service

    def _cpu_composite(self) -> CanvasCpuCompositeService:
        service = getattr(self, "_cpu_composite_service", None)
        if not isinstance(service, CanvasCpuCompositeService):
            service = CanvasCpuCompositeService(self)
            self._cpu_composite_service = service
        return service

    def _filter_apply(self) -> CanvasLayerImageService:
        service = getattr(self, "_filter_apply_service", None)
        if not isinstance(service, CanvasLayerImageService):
            service = CanvasLayerImageService(
                self,
                filter_registry=FilterRegistry,
                layer_type=_Layer,
                events=Events,
            )
            self._filter_apply_service = service
        return service

    _effect_apply = _filter_apply

    @property
    def _effect_apply_service(self):
        return getattr(self, "_filter_apply_service", None)

    @_effect_apply_service.setter
    def _effect_apply_service(self, value):
        self._filter_apply_service = value

    @property
    def _effect_cache_bytes(self):
        return getattr(self, "_filter_cache_bytes", 0)

    @_effect_cache_bytes.setter
    def _effect_cache_bytes(self, value):
        self._filter_cache_bytes = value

    @property
    def _effect_pen_service(self):
        return getattr(self, "_filter_pen_service", None)

    @_effect_pen_service.setter
    def _effect_pen_service(self, value):
        self._filter_pen_service = value

    def _ai_effect_state(self) -> CanvasAIEffectStateService:
        service = getattr(self, "_ai_effect_state_service", None)
        if not isinstance(service, CanvasAIEffectStateService):
            service = CanvasAIEffectStateService(
                self,
                layer_type=_Layer,
                events=Events,
            )
            self._ai_effect_state_service = service
        return service

    def _history(self) -> CanvasHistoryService:
        service = getattr(self, "_history_service", None)
        if not isinstance(service, CanvasHistoryService):
            service = CanvasHistoryService(
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
            self._history_service = service
        return service

    def _history_port(self) -> CanvasHistoryPortService:
        service = getattr(self, "_history_port_service", None)
        if not isinstance(service, CanvasHistoryPortService):
            service = CanvasHistoryPortService(self)
            self._history_port_service = service
        return service

    def _vector_text_render(self) -> VectorTextRenderService:
        service = getattr(self, "_vector_text_render_service", None)
        if not isinstance(service, VectorTextRenderService):
            service = VectorTextRenderService(self)
            self._vector_text_render_service = service
        return service

    def _geometry(self) -> CanvasGeometryService:
        service = getattr(self, "_geometry_service", None)
        if not isinstance(service, CanvasGeometryService):
            service = CanvasGeometryService(
                self,
                resize_snapshot_type=_ResizeSnapshot,
                resize_entry_type=_ResizeEntry,
                history_group_type=_HistoryGroup,
                compress_qimage=_compress_qimage,
                decompress_qimage=_decompress_qimage,
            )
            self._geometry_service = service
        return service

    def _resample(self) -> CanvasResampleService:
        service = getattr(self, "_resample_service", None)
        if not isinstance(service, CanvasResampleService):
            service = CanvasResampleService(self)
            self._resample_service = service
        return service

    def _layer_snapshot(self) -> CanvasLayerSnapshotService:
        service = getattr(self, "_layer_snapshot_service", None)
        if not isinstance(service, CanvasLayerSnapshotService):
            service = CanvasLayerSnapshotService(self)
            self._layer_snapshot_service = service
        return service

    def _layer_content_bounds(self) -> CanvasLayerContentBoundsService:
        service = getattr(self, "_layer_content_bounds_service", None)
        if not isinstance(service, CanvasLayerContentBoundsService):
            service = CanvasLayerContentBoundsService(self)
            self._layer_content_bounds_service = service
        return service

    def _layer_content_mutation(self) -> CanvasLayerContentMutationService:
        service = getattr(self, "_layer_content_mutation_service", None)
        if not isinstance(service, CanvasLayerContentMutationService):
            service = CanvasLayerContentMutationService(self)
            self._layer_content_mutation_service = service
        return service

    def _document_events(self) -> CanvasDocumentEventService:
        service = getattr(self, "_document_event_service", None)
        if not isinstance(service, CanvasDocumentEventService):
            service = CanvasDocumentEventService(self)
            self._document_event_service = service
        return service

    def _layer_events(self) -> CanvasLayerEventService:
        service = getattr(self, "_layer_event_service", None)
        if not isinstance(service, CanvasLayerEventService):
            service = CanvasLayerEventService(self)
            self._layer_event_service = service
        return service

    def _layer_query(self) -> CanvasLayerQueryService:
        service = getattr(self, "_layer_query_service", None)
        if not isinstance(service, CanvasLayerQueryService):
            service = CanvasLayerQueryService(self)
            self._layer_query_service = service
        return service

    def _layer_mask(self) -> CanvasLayerMaskService:
        service = getattr(self, "_layer_mask_service", None)
        if not isinstance(service, CanvasLayerMaskService):
            service = CanvasLayerMaskService(self)
            self._layer_mask_service = service
        return service

    def _layer_property(self) -> CanvasLayerPropertyService:
        service = getattr(self, "_layer_property_service", None)
        if not isinstance(service, CanvasLayerPropertyService):
            service = CanvasLayerPropertyService(self)
            self._layer_property_service = service
        return service

    def _layer_rasterization(self) -> CanvasLayerRasterizationService:
        service = getattr(self, "_layer_rasterization_service", None)
        if not isinstance(service, CanvasLayerRasterizationService):
            service = CanvasLayerRasterizationService(self)
            self._layer_rasterization_service = service
        return service

    def _layer_transform_preview(self) -> CanvasLayerTransformPreviewService:
        service = getattr(self, "_layer_transform_preview_service", None)
        if not isinstance(service, CanvasLayerTransformPreviewService):
            service = CanvasLayerTransformPreviewService(self)
            self._layer_transform_preview_service = service
        return service

    def _mask_layer(self) -> CanvasMaskLayerService:
        service = getattr(self, "_mask_layer_service", None)
        if not isinstance(service, CanvasMaskLayerService):
            service = CanvasMaskLayerService(self)
            self._mask_layer_service = service
        return service

    def _mask_stroke_commit(self) -> CanvasMaskStrokeCommitService:
        service = getattr(self, "_mask_stroke_commit_service", None)
        if not isinstance(service, CanvasMaskStrokeCommitService):
            service = CanvasMaskStrokeCommitService(self)
            self._mask_stroke_commit_service = service
        return service

    def _mask_stroke(self) -> CanvasMaskStrokeService:
        service = getattr(self, "_mask_stroke_service", None)
        if not isinstance(service, CanvasMaskStrokeService):
            service = CanvasMaskStrokeService(self)
            self._mask_stroke_service = service
        return service

    def _merge_flatten(self) -> CanvasMergeFlattenService:
        service = getattr(self, "_merge_flatten_service", None)
        if not isinstance(service, CanvasMergeFlattenService):
            service = CanvasMergeFlattenService(
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
            self._merge_flatten_service = service
        return service

    def _pixmap_readback(self) -> CanvasPixmapReadbackService:
        service = getattr(self, "_pixmap_readback_service", None)
        if not isinstance(service, CanvasPixmapReadbackService):
            service = CanvasPixmapReadbackService(self)
            self._pixmap_readback_service = service
        return service

    def _render_invalidation(self) -> CanvasRenderInvalidationService:
        service = getattr(self, "_render_invalidation_service", None)
        if not isinstance(service, CanvasRenderInvalidationService):
            service = CanvasRenderInvalidationService(self)
            self._render_invalidation_service = service
        return service

    def _runtime_snapshot(self) -> CanvasRuntimeSnapshotService:
        service = getattr(self, "_runtime_snapshot_service", None)
        if not isinstance(service, CanvasRuntimeSnapshotService):
            service = CanvasRuntimeSnapshotService(self)
            self._runtime_snapshot_service = service
        return service

    def _selection_change_submit(self) -> CanvasSelectionChangeSubmitService:
        service = getattr(self, "_selection_change_submit_service", None)
        if not isinstance(service, CanvasSelectionChangeSubmitService):
            service = CanvasSelectionChangeSubmitService(self)
            self._selection_change_submit_service = service
        return service

    def _selection_content_submit(self) -> CanvasSelectionContentSubmitService:
        service = getattr(self, "_selection_content_submit_service", None)
        if not isinstance(service, CanvasSelectionContentSubmitService):
            service = CanvasSelectionContentSubmitService(self)
            self._selection_content_submit_service = service
        return service

    def _selection_history(self) -> CanvasSelectionHistoryService:
        service = getattr(self, "_selection_history_service", None)
        if not isinstance(service, CanvasSelectionHistoryService):
            service = CanvasSelectionHistoryService(
                self,
                patch_factory=_HistoryPatch.from_images,
                content_layer_type=_Layer,
            )
            self._selection_history_service = service
        return service

    def _selection_mask_submit(self) -> CanvasSelectionMaskSubmitService:
        service = getattr(self, "_selection_mask_submit_service", None)
        if not isinstance(service, CanvasSelectionMaskSubmitService):
            service = CanvasSelectionMaskSubmitService(self)
            self._selection_mask_submit_service = service
        return service

    def _layer_visibility_submit(self) -> CanvasLayerVisibilitySubmitService:
        service = getattr(self, "_layer_visibility_submit_service", None)
        if not isinstance(service, CanvasLayerVisibilitySubmitService):
            service = CanvasLayerVisibilitySubmitService(
                self,
                layer_changed_event=Events.LAYER_CHANGED,
            )
            self._layer_visibility_submit_service = service
        return service

    def _simple_layer_command_submit(self) -> CanvasSimpleLayerCommandSubmitService:
        service = getattr(self, "_simple_layer_command_submit_service", None)
        if not isinstance(service, CanvasSimpleLayerCommandSubmitService):
            service = CanvasSimpleLayerCommandSubmitService(self)
            self._simple_layer_command_submit_service = service
        return service

    def _alpha_preservation(self) -> CanvasAlphaPreservationService:
        service = getattr(self, "_alpha_preservation_service", None)
        if not isinstance(service, CanvasAlphaPreservationService):
            service = CanvasAlphaPreservationService(self)
            self._alpha_preservation_service = service
        return service

    def _apply_image_service_ref(self) -> CanvasApplyImageService:
        service = getattr(self, "_apply_image_service", None)
        if not isinstance(service, CanvasApplyImageService):
            service = CanvasApplyImageService(self)
            self._apply_image_service = service
        return service

    def _command_dirty_region(self) -> CanvasCommandDirtyRegionService:
        service = getattr(self, "_command_dirty_region_service", None)
        if not isinstance(service, CanvasCommandDirtyRegionService):
            service = CanvasCommandDirtyRegionService(self)
            self._command_dirty_region_service = service
        return service

    def _command_routing(self) -> CanvasCommandRoutingService:
        service = getattr(self, "_command_routing_service", None)
        if not isinstance(service, CanvasCommandRoutingService):
            service = CanvasCommandRoutingService(self)
            self._command_routing_service = service
        return service

    def _fill_gradient_submit(self) -> CanvasFillGradientSubmitService:
        service = getattr(self, "_fill_gradient_submit_service", None)
        if not isinstance(service, CanvasFillGradientSubmitService):
            service = CanvasFillGradientSubmitService(
                self,
                flood_fill_to_new_layer_type=FloodFillToNewLayerCommand,
                pattern_fill_type=PatternFillCommand,
            )
            self._fill_gradient_submit_service = service
        return service

    def _layer_bounds(self) -> CanvasLayerBoundsService:
        service = getattr(self, "_layer_bounds_service", None)
        if not isinstance(service, CanvasLayerBoundsService):
            service = CanvasLayerBoundsService(self)
            self._layer_bounds_service = service
        return service

    def _stroke_application(self) -> CanvasStrokeApplicationService:
        service = getattr(self, "_stroke_application_service", None)
        if not isinstance(service, CanvasStrokeApplicationService):
            service = CanvasStrokeApplicationService(self)
            self._stroke_application_service = service
        return service

    def _stroke_draw_primitives(self) -> CanvasStrokeDrawPrimitiveService:
        service = getattr(self, "_stroke_draw_primitive_service", None)
        if not isinstance(service, CanvasStrokeDrawPrimitiveService):
            service = CanvasStrokeDrawPrimitiveService()
            self._stroke_draw_primitive_service = service
        return service

    def _stroke_layer_paint(self) -> CanvasStrokeLayerPaintService:
        service = getattr(self, "_stroke_layer_paint_service", None)
        if not isinstance(service, CanvasStrokeLayerPaintService):
            service = CanvasStrokeLayerPaintService(self)
            self._stroke_layer_paint_service = service
        return service

    def _stroke_pixel_commit(self) -> CanvasStrokePixelCommitService:
        service = getattr(self, "_stroke_pixel_commit_service", None)
        if not isinstance(service, CanvasStrokePixelCommitService):
            service = CanvasStrokePixelCommitService(self)
            self._stroke_pixel_commit_service = service
        return service

    def _stroke_stamp_brush(self) -> CanvasStrokeStampBrushService:
        service = getattr(self, "_stroke_stamp_brush_service", None)
        if not isinstance(service, CanvasStrokeStampBrushService):
            service = CanvasStrokeStampBrushService(self)
            self._stroke_stamp_brush_service = service
        return service

    def _stroke_submit(self) -> CanvasStrokeSubmitService:
        service = getattr(self, "_stroke_submit_service", None)
        if not isinstance(service, CanvasStrokeSubmitService):
            service = CanvasStrokeSubmitService(self)
            self._stroke_submit_service = service
        return service

    def _stroke_target(self) -> CanvasStrokeTargetService:
        service = getattr(self, "_stroke_target_service", None)
        if not isinstance(service, CanvasStrokeTargetService):
            service = CanvasStrokeTargetService(self)
            self._stroke_target_service = service
        return service

    def _transform_image(self) -> CanvasTransformImageService:
        service = getattr(self, "_transform_image_service", None)
        if not isinstance(service, CanvasTransformImageService):
            service = CanvasTransformImageService()
            self._transform_image_service = service
        return service

    def _transform_submit(self) -> CanvasTransformSubmitService:
        service = getattr(self, "_transform_submit_service", None)
        if not isinstance(service, CanvasTransformSubmitService):
            service = CanvasTransformSubmitService(self)
            self._transform_submit_service = service
        return service

    def _tree_command_submit(self) -> CanvasTreeCommandSubmitService:
        service = getattr(self, "_tree_command_submit_service", None)
        if not isinstance(service, CanvasTreeCommandSubmitService):
            service = CanvasTreeCommandSubmitService(self)
            self._tree_command_submit_service = service
        return service

    def _vector_command_submit(self) -> CanvasVectorCommandSubmitService:
        service = getattr(self, "_vector_command_submit_service", None)
        if not isinstance(service, CanvasVectorCommandSubmitService):
            service = CanvasVectorCommandSubmitService(
                self,
                doc_changed_event=Events.DOC_CHANGED,
            )
            self._vector_command_submit_service = service
        return service
