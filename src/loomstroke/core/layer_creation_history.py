"""Ownership of one created layer across standard Add/Duplicate Undo and Redo."""

from __future__ import annotations

import logging
from inspect import getattr_static
from typing import Any

from .commands_layer_lookup import find_layer
from .events import Events
from .layer_history_memory import estimate_retained_layer_bytes

logger = logging.getLogger(__name__)


class CreatedLayerHistory:
    """Retain the payload and placement, never a snapshot of the whole canvas."""

    def __init__(self) -> None:
        self.layer: Any | None = None
        self.parent_id: str | None = None
        self.index = 0
        self.active_before: str | None = None
        self._ever_attached = False

    @staticmethod
    def supports(canvas: Any) -> bool:
        tree = getattr(canvas, "_layer_tree", None)
        return (
            isinstance(getattr(canvas, "_layers", None), list)
            and callable(tree)
            and callable(getattr(tree(), "_prepare_layer_for_history", None))
        )

    @staticmethod
    def _active(canvas: Any) -> str | None:
        return canvas.current_layer().id if canvas._layers else None

    @staticmethod
    def _refresh(canvas: Any) -> None:
        from .layer_tree_service import LayerTreeQueryService, sync_active_markers  # noqa: PLC0415

        sync_active_markers(canvas, LayerTreeQueryService(canvas))
        canvas._invalidate_layer_cache()
        canvas._recompose()

    def create(
        self, canvas: Any, name: str, layer_type: str, values: dict[str, Any] | None = None
    ) -> bool:
        try:
            if self.layer is None:
                active = self._active(canvas)
                layer = canvas._layer_tree()._prepare_layer_for_history(name, layer_type=layer_type)
                for key, value in (values or {}).items():
                    setattr(layer, key, value)
                self.layer, self.index, self.active_before = layer, len(canvas._layers), active
            return self.restore(canvas)
        except Exception as exc:
            logger.warning("Layer preparation failed (%s)", type(exc).__name__)
            return False

    def _siblings(self, canvas: Any) -> list[Any] | None:
        if self.parent_id is None:
            return canvas._layers
        parent = find_layer(canvas, self.parent_id)
        return parent.children if parent is not None and parent.layer_type == "group" else None

    def restore(self, canvas: Any) -> bool:
        layer = self.layer
        if layer is None or find_layer(canvas, layer.id) is not None:
            return False
        siblings = self._siblings(canvas)
        if siblings is None:
            return False
        roots, previous = canvas._layers, self._active(canvas)
        try:
            siblings.insert(min(self.index, len(siblings)), layer)
            layer.parent_id = self.parent_id
            canvas._active_layer_id = layer.id
            self._refresh(canvas)
            if canvas._layers is not roots or find_layer(canvas, layer.id) is not layer:
                return False
            from loomstroke.host_api import LayerRef  # noqa: PLC0415

            canvas._publish_layer_event(
                Events.LAYER_ADDED,
                layer=layer,
                change_type="restored" if self._ever_attached else "added",
                new_value=LayerRef(
                    id=layer.id,
                    name=layer.name,
                    layer_type=layer.layer_type,
                    vector_kind=layer.vector_kind,
                ),
            )
            succeeded = canvas._layers is roots and find_layer(canvas, layer.id) is layer
            self._ever_attached = self._ever_attached or succeeded
            return succeeded
        except Exception as exc:
            if canvas._layers is roots:
                for index, candidate in enumerate(siblings):
                    if candidate is layer:
                        siblings.pop(index)
                        break
                self._repair_active(canvas, previous)
            logger.warning("Created layer insertion failed (%s)", type(exc).__name__)
            return False

    def _repair_active(self, canvas: Any, identifier: str | None) -> bool:
        try:
            if identifier is not None and find_layer(canvas, identifier) is not None:
                canvas._active_layer_id = identifier
            self._refresh(canvas)
            return True
        except Exception as exc:
            logger.warning("Created layer rollback refresh failed (%s)", type(exc).__name__)
            return False

    def remove(self, canvas: Any) -> bool:
        live = find_layer(canvas, self.layer.id) if self.layer is not None else None
        if live is None:
            return False
        location = canvas._find_parent(live.id)
        if location is None:
            return False
        # Page hydration legitimately rebuilds runtime objects with the same ID.
        # Take custody of the live payload, keeping the original redo placement.
        self.layer = live
        siblings, index = location
        roots, previous = canvas._layers, self._active(canvas)
        try:
            # Commands import this helper before the Canvas mixin is defined.
            from .canvas_service_simple_layer_api import _DEFAULT_REMOVE_LAYER  # noqa: PLC0415

            removed = (
                canvas._remove_created_layer_for_history(live)
                if getattr_static(canvas, "remove_layer", None) is _DEFAULT_REMOVE_LAYER
                else canvas.remove_layer(live.id)
            )
            if not removed:
                return False
            if canvas._layers is not roots:
                return False
            if not self._repair_active(canvas, self.active_before):
                raise RuntimeError("Created layer removal refresh failed")
            return canvas._layers is roots and find_layer(canvas, live.id) is None
        except Exception as exc:
            if canvas._layers is roots and find_layer(canvas, live.id) is None:
                siblings.insert(index, live)
                self._repair_active(canvas, previous)
            logger.warning("Created layer removal failed (%s)", type(exc).__name__)
            return False

    @property
    def retained_bytes(self) -> int:
        return estimate_retained_layer_bytes(self.layer)
