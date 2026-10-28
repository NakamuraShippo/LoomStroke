"""Retain the standard canvas model without claiming page or history custody.

Page-switch and master-command owners supply their own scope checks and history
receipts. Recovery is assignment-only: no installer, finalizer, redraw, or user
callback is called. Source layer objects and their image/native payloads must
remain untouched while the owner installs a separate candidate tree.
"""

from __future__ import annotations

from typing import Any

from .canvas_invalidation import GpuResourceInvalidation

_MODEL_FIELDS = (
    "_doc",
    "_layers",
    "_active_index",
    "_active_layer_id",
    "_width",
    "_height",
    "_dpi",
    "_background",
    "_max_layers",
    "_composited",
    "_prev_composited",
    "_selection_mask",
    "_selection_bounds",
    "_vector_selection",
    "_vector_preview",
    "_vector_preview_layer_id",
    "_pending_panel_drawing",
    "_filter_cache_bytes",
)


class _CanvasModelRecovery:
    """One retained model and fresh render caches; notifications stay outside.

    This deliberately excludes content stacks, owner/generation, pending history
    group/transaction state, the document-capture gate, and page/book state.
    The surrounding operation owns those states and determines whether restoring
    this retained model is still allowed after a callback.
    """

    def __init__(self, canvas: Any, *, reason: str = "canvas_model_recovery") -> None:
        # The existing plain-state bundle imports PageService. The local import
        # lets its initialization finish before this shared lower receipt is used.
        from .image_document_state import ImageDocumentStateBundle  # noqa: PLC0415

        self._canvas = canvas
        self._bundles = [ImageDocumentStateBundle.prepare(canvas, dict.fromkeys(_MODEL_FIELDS))]
        for name, fields in (
            ("_channels_service", ("_selected", "_layer_id", "_color_layer_id", "_view")),
            ("_native_cmyk_service", ("selected", "_foreground")),
        ):
            target = getattr(canvas, name, None)
            if target is not None:
                self._bundles.append(
                    ImageDocumentStateBundle.prepare(target, dict.fromkeys(fields))
                )
        # Cache dictionaries can be pruned in place. Fresh caches avoid keeping
        # partial target entries; all retained source layers remain unchanged.
        self._fresh_render = {
            "_qpath_cache": {},
            "_vector_raster_cache": {},
            "_gpu_dirty_rect": None,
            "_gpu_mask_dirty_layers": set(),
            "_gpu_full_recompose": True,
            "_gpu_resource_invalidation": GpuResourceInvalidation.for_all(reason=reason),
        }

    def restore(self) -> None:
        cache_version = self._canvas._layer_cache_version + 1
        for bundle in self._bundles:
            bundle.restore()
        self._canvas.__dict__.update(self._fresh_render)
        # Published cache generations must never move backwards. History-owner
        # generations are a separate fence maintained by the surrounding owner.
        self._canvas.__dict__["_layer_cache_version"] = cache_version
