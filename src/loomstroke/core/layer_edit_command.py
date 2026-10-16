"""Undoable MCP layer edits, with copy-on-write pixels and stable tree identities."""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.commands import Command
from loomstroke.core.events import Events
from loomstroke.core.japanese_text.story_geometry import refresh_story_geometry

# Only edited layers own content snapshots. Tree snapshots retain references,
# not copies of every image. QImage's implicit sharing detaches on mutation.
_FIELDS = (
    "visible",
    "locked",
    "alpha_lock",
    "clipping",
    "image",
    "layer_mask",
    "mask_enabled",
    "mask_edit_mode",
    "layer_type",
    "vector_kind",
    "vector_objects",
    "offset_x",
    "offset_y",
    "transform",
    "_preview_transform",
    "opacity",
    "blend_mode",
    "metadata",
)


def _copy_value(value: Any) -> Any:
    if isinstance(value, QImage):
        return QImage(value)
    if isinstance(value, list):
        return list(value)
    return copy.deepcopy(value)


@dataclass
class _Snapshot:
    roots: list[Any]
    tree: list[tuple[Any, list[Any], str | None]]
    content: list[tuple[Any, dict[str, Any]]]
    active_id: str | None
    vector_selection: Any
    vector_preview: Any
    vector_preview_layer_id: str | None
    ai_refs: list[tuple[Any, dict[str, Any]]]

    @classmethod
    def capture(cls, canvas: Any, content_ids: set[str]) -> _Snapshot:
        layers = canvas._flatten_layers()
        return cls(
            roots=list(canvas._layers),
            tree=[(layer, list(layer.children), layer.parent_id) for layer in layers],
            content=[
                (layer, {key: _copy_value(getattr(layer, key)) for key in _FIELDS})
                for layer in layers
                if layer.id in content_ids
            ],
            active_id=canvas.current_layer().id,
            vector_selection=copy.deepcopy(getattr(canvas, "_vector_selection", None)),
            vector_preview=getattr(canvas, "_vector_preview", None),
            vector_preview_layer_id=getattr(canvas, "_vector_preview_layer_id", None),
            ai_refs=[
                (layer, copy.deepcopy(layer.ai_effect.slot_refs))
                for layer in layers
                if getattr(layer, "ai_effect", None) is not None
                and isinstance(getattr(layer.ai_effect, "slot_refs", None), dict)
            ],
        )

    def restore(self, canvas: Any) -> None:
        previous_refs = {
            layer.id: dict(layer.ai_effect.slot_refs)
            for layer in canvas._flatten_layers()
            if getattr(layer, "ai_effect", None) is not None
            and isinstance(getattr(layer.ai_effect, "slot_refs", None), dict)
        }
        restored_ai_ids = {layer.id for layer, _ in self.ai_refs}
        for layer_id, refs in previous_refs.items():
            if layer_id not in restored_ai_ids:
                canvas._sync_registry_for_slot_refs(layer_id, refs, {})
        canvas._layers[:] = self.roots
        for layer, children, parent_id in self.tree:
            layer.children[:] = children
            layer.parent_id = parent_id
        for layer, values in self.content:
            for key, value in values.items():
                setattr(layer, key, _copy_value(value))
        for layer, refs in self.ai_refs:
            canvas._sync_registry_for_slot_refs(layer.id, previous_refs.get(layer.id, {}), refs)
            layer.ai_effect.slot_refs = copy.deepcopy(refs)
        canvas._invalidate_layer_cache()
        if self.active_id is not None:
            canvas.set_active_layer(self.active_id)
        canvas._vector_selection = copy.deepcopy(self.vector_selection)
        canvas._vector_preview = self.vector_preview
        canvas._vector_preview_layer_id = self.vector_preview_layer_id


class LayerEditCommand(Command):
    """Use the existing CanvasChangeset generic Command submit route.

    ``vector_object`` is the existing routing marker; the sentinel prevents
    that route from implicitly creating a vector layer. No history internals
    are edited. Snapshot restoration never restores texture version counters.
    """

    vector_object = object()
    _layer_id = "__loomstroke_mcp_layer_edit__"

    def __init__(
        self, target_ids: Iterable[str], operation: Callable[[Any], dict[str, Any]]
    ) -> None:
        super().__init__()
        self.target_ids = set(target_ids)
        self.operation = operation
        self.result: dict[str, Any] = {}
        self.before: _Snapshot | None = None
        self.after: _Snapshot | None = None

    def apply(self, canvas: Any) -> bool:
        if self.after is not None:
            self.after.restore(canvas)
            self._notify(canvas, self.before, self.after)
            self._executed = True
            return True
        self.before = _Snapshot.capture(canvas, self.target_ids)
        try:
            self.result = self.operation(canvas)
            self.after = _Snapshot.capture(canvas, self.target_ids)
            self._notify(canvas, self.before, self.after)
        except Exception:
            self.before.restore(canvas)
            self.after = None
            self._notify(canvas, self.before, self.before)
            self._executed = False
            raise
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed or self.before is None:
            return False
        self.before.restore(canvas)
        self._notify(canvas, self.after, self.before)
        self._executed = False
        return True

    def _notify(self, canvas: Any, before: _Snapshot | None, after: _Snapshot | None) -> None:
        old = {layer.id: (layer, children) for layer, children, _ in before.tree} if before else {}
        new = {layer.id: (layer, children) for layer, children, _ in after.tree} if after else {}
        affected = set(self.target_ids) | (old.keys() ^ new.keys())
        affected.update(
            key
            for key in old.keys() & new.keys()
            if [child.id for child in old[key][1]] != [child.id for child in new[key][1]]
        )
        changed_objects = []
        for snapshot in (before, after):
            if snapshot is not None:
                for _, values in snapshot.content:
                    changed_objects.extend(values["vector_objects"])
                for layer, _, _ in snapshot.tree:
                    if layer.id in old.keys() ^ new.keys():
                        changed_objects.extend(layer.vector_objects)
        # Membership changes must also update linked text frames on other
        # layers. The core helper deterministically rebuilds derived frames
        # and increments versions for any remaining text objects it replaces.
        refresh_story_geometry(canvas, changed_objects)
        for layer_id in tuple(affected):
            layer = canvas._get_layer_by_id(layer_id)
            while layer is not None and layer.parent_id is not None:
                affected.add(layer.parent_id)
                layer = canvas._get_layer_by_id(layer.parent_id)
        canvas.bump_layer_texture_versions(tuple(affected))
        for layer_id in affected:
            layer = canvas._get_layer_by_id(layer_id)
            if layer is not None:
                layer.transform_version += 1
                canvas._publish_layer_event(
                    Events.LAYER_CHANGED, layer=layer, change_type="mcp_edit"
                )
                canvas._emit_layer_content_changed(
                    layer, QRect(0, 0, canvas._width, canvas._height), {"source": "mcp_layer_edit"}
                )
        for layer_id in old.keys() ^ new.keys():
            layer = (new if layer_id in new else old)[layer_id][0]
            event = Events.LAYER_ADDED if layer_id in new else Events.LAYER_REMOVED
            canvas._publish_layer_event(event, layer=layer, change_type="mcp_tree_edit")
        canvas._invalidate_layer_cache()
        canvas._recompose()
