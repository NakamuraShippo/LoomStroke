"""Explicit, reversible runtime state bundles for an image-document boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .canvas_history_restore_state import HistoryRestoreOwner
from .canvas_invalidation import GpuResourceInvalidation
from .document_metadata_service import DocumentMetadataService
from .page_residency import PageResidencyGuard
from .page_service import _PageState

_MISSING = object()
_CANVAS_FIELDS = (
    "_doc",
    "_layers",
    "_active_index",
    "_active_layer_id",
    "_width",
    "_height",
    "_background",
    "_dpi",
    "_max_layers",
    "color_profile",
    "_composited",
    "_prev_composited",
    "_defer_recompose_depth",
    "_defer_recompose_needed",
    "_mask_paint_dirty",
    "_mask_paint_layer_id",
    "_mask_paint_mask_snapshot",
    "_gpu_mask_dirty_layers",
    "_filter_cache_bytes",
    "_in_transaction",
    "_transaction_label",
    "_active_history_group",
    "_mask_edit_flag",
    "_stroke_painting_active",
    "_undo_stack",
    "_redo_stack",
    "_vector_preview_layer_id",
    "_selection_mask",
    "_selection_bounds",
    "_selection_id",
    "_selection_mask_full",
    "_selection_preview_image",
    "_selection_preview_bounds",
    "_gpu_dirty_rect",
    "_qpath_cache",
    "_vector_raster_cache",
)
_PREIMAGE_FIELDS = (
    "_mode",
    "_layer_id",
    "_img_key",
    "_offset",
    "_mask_edit",
    "_dirty",
    "_source",
    "_dense_before",
    "_fragments",
    "_captured_logical_bytes",
    "_dense_snapshot_count",
)


@dataclass
class ImageDocumentStateBundle:
    """Retain exact old references; no old pixels or mutable containers are copied."""

    target: Any
    values: dict[str, Any]
    before: dict[str, Any]

    @classmethod
    def prepare(cls, target: Any, values: dict[str, Any]) -> ImageDocumentStateBundle:
        return cls(target, values, {name: target.__dict__.get(name, _MISSING) for name in values})

    def restore(self) -> None:
        # Bypass the installer that failed. Every target here owns plain Python
        # state, and none of these names is a descriptor or QObject property.
        state = self.target.__dict__
        for name, value in self.before.items():
            if value is _MISSING:
                state.pop(name, None)
            else:
                state[name] = value


def image_document_state_bundles(owner: Any, prepared: Any) -> list[ImageDocumentStateBundle]:
    """Allocate the entire assignment plan before replacing any live field."""
    canvas, stage = owner._canvas, prepared.canvas
    values = {name: getattr(stage, name) for name in _CANVAS_FIELDS}
    values.update(
        _layer_cache_version=canvas._layer_cache_version + 1,
        _gpu_full_recompose=True,
        _gpu_resource_invalidation=GpuResourceInvalidation.for_all(reason="image_document_open"),
        _native_selection_preview=None,
        _vector_preview=None,
        _vector_selection=None,
        _pending_panel_drawing=None,
        _ai_effect_output_tombstones={},
    )
    bundles = [ImageDocumentStateBundle.prepare(canvas, values)]
    bundles.append(
        ImageDocumentStateBundle.prepare(
            canvas._history_service,
            {
                "_restore_owner": HistoryRestoreOwner(),
                "_restore_generation": canvas._history_service._restore_generation + 1,
            },
        )
    )
    bundles.append(
        ImageDocumentStateBundle.prepare(
            canvas._txn_preimage_capture,
            {name: getattr(stage._txn_preimage_capture, name) for name in _PREIMAGE_FIELDS},
        )
    )
    bundles.append(
        ImageDocumentStateBundle.prepare(
            canvas._render_invalidation_service, {"_story_preview_layers": set()}
        )
    )
    for name, state in (
        (
            "_channels_service",
            {"_selected": (0, 1, 2), "_layer_id": None, "_color_layer_id": None, "_view": None},
        ),
        (
            "_native_cmyk_service",
            {"_foreground": None, "selected": (0, 1, 2, 3), "_color_cache": {}},
        ),
        ("_reference_registry", {"_back_refs": {}}),
        ("_document_capture_gate_state", {"tokens": set()}),
    ):
        target = getattr(canvas, name, None)
        if target is not None:
            bundles.append(ImageDocumentStateBundle.prepare(target, state))
    pages = owner._page_service
    if pages is not None:
        bundles.extend(_page_bundles(pages, prepared))
    document_state = (
        None
        if pages is not None
        else owner._make_document_state(prepared.metadata, [prepared.page], prepared.page.id)
    )
    bundles.append(
        ImageDocumentStateBundle.prepare(
            owner._metadata_service,
            {
                "_loaded_metadata": prepared.metadata,
                "_document_state": document_state,
            },
        )
    )
    bundles.append(
        ImageDocumentStateBundle.prepare(
            owner._persistence_service,
            {
                "_save_attempt": None,
                "_deferred_autosave": None,
            },
        )
    )
    return bundles


def _page_bundles(pages: Any, prepared: Any) -> list[ImageDocumentStateBundle]:
    book = pages._book_history
    return [
        ImageDocumentStateBundle.prepare(
            pages,
            {
                "_pages": [_PageState(prepared.page)],
                "_active_page_id": prepared.page.id,
                "_document_canvas": DocumentMetadataService.host_canvas_spec_from_metadata(
                    prepared.metadata
                ),
                "_page_thumbnail_pngs": {},
                "_page_hydration_loader": None,
                "_page_hydration_source": None,
                "_page_residency": PageResidencyGuard(),
                "_page_access_counter": 0,
            },
        ),
        ImageDocumentStateBundle.prepare(
            pages._page_history,
            {
                "_bundles": {},
                "_ordinals": {},
                "_next_ordinal": 0,
            },
        ),
        ImageDocumentStateBundle.prepare(
            book,
            {
                "_undo": [],
                "_redo": [],
                "_trim_generation": book._trim_generation + 1,
                "_pending_trim_generation": None,
            },
        ),
        ImageDocumentStateBundle.prepare(pages._history_sequence, {"_next": 1}),
        ImageDocumentStateBundle.prepare(pages._history_state_notifier, {"_dirty": False}),
    ]
