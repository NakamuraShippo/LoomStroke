"""Own a system paste's new RGB layer and selection as one history entry."""

from __future__ import annotations

from .commands_layer_lookup import find_layer
from .commands_selection_content import PasteSelectionCommand
from .events import DocumentChangeEvent
from .layer_creation_history import CreatedLayerHistory
from .resource_place_command import supports_prepared_rgb_import
from .selection_state import build_selection_state_snapshot


class PasteToNewLayerCommand(PasteSelectionCommand):
    history_label = "Paste Selection"
    change_type = "selection.paste"

    def __init__(self, payload, position, *, name="Pasted Layer") -> None:
        super().__init__(payload, position=position)
        self._name = name
        self._created = CreatedLayerHistory()
        self._submitting = False
        self._selection_at_start = None
        self._full_bounds_at_start = None

    @staticmethod
    def supports(canvas) -> bool:
        return (
            CreatedLayerHistory.supports(canvas)
            and supports_prepared_rgb_import(canvas, True)
            and not any(layer.native_cmyk is not None for layer in canvas._flatten_layers())
        )

    @property
    def retained_bytes(self) -> int:
        images = (
            self._before_layer,
            self._after_layer,
            getattr(self._before_selection, "mask", None),
            getattr(self._after_selection, "mask", None),
            getattr(self._selection_at_start, "mask", None),
            getattr(self._selection_at_start, "preview_image", None),
        )
        arrays = (
            self.payload.image,
            self.payload.mask,
            self._before_full_mask,
            getattr(self._selection_at_start, "mask_full", None),
        )
        return (
            self._created.retained_bytes
            + sum(image.sizeInBytes() for image in images if image is not None)
            + sum(getattr(array, "nbytes", 0) for array in arrays)
        )

    def _prepare(self, canvas) -> None:
        layer = canvas._layer_tree()._prepare_layer_for_history(self._name, layer_type="raster")
        self._created.layer = layer
        self._created.index = len(canvas._layers)
        self._created.active_before = canvas.current_layer().id if canvas._layers else None
        self.layer_id = layer.id
        self._selection_at_start = build_selection_state_snapshot(canvas)
        self._full_bounds_at_start = getattr(canvas, "_selection_bounds_full", None)

    def apply(self, canvas) -> bool:
        if self._created.layer is None:
            self._prepare(canvas)
        if not self._created.restore(canvas) or not super().apply(canvas):
            return False
        canvas._bump_active_texture_version()
        canvas._recompose()
        self._notify(canvas)
        return True

    def revert(self, canvas) -> bool:
        live = find_layer(canvas, self.layer_id)
        if live is None:
            return False
        self._layer_ref = live
        if not super().revert(canvas):
            return False
        live.texture_version += 1
        if not self._created.remove(canvas):
            super().apply(canvas)
            return False
        self._notify(canvas)
        return True

    def _rollback(self, canvas) -> bool:
        live = find_layer(canvas, self.layer_id) if self.layer_id else None
        if live is not None and not self._created.remove(canvas):
            return False
        state = self._selection_at_start
        if state is not None:
            canvas._selection_mask = state.mask
            canvas._selection_bounds = state.bounds
            canvas._selection_mask_full = state.mask_full
            canvas._selection_bounds_full = self._full_bounds_at_start
            canvas._selection_preview_image = state.preview_image
            canvas._selection_preview_bounds = state.preview_bounds
            canvas._selection_state().emit_selection_changed()
        self._executed = False
        return True

    def _notify(self, canvas) -> None:
        if not self._submitting:
            canvas._emit_doc_changed(DocumentChangeEvent(change_type=self.change_type))
