"""Stable prepared layer insertions for panel parts and content units."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.command_types import Command
from loomstroke.core.layer_tree_service import LayerTreeQueryService, sync_active_markers

from .membership import PART_KEY
from .models import PanelFrameError


class PanelLayerInsertCommand(Command):
    """Install detached attributes once; Undo/Redo reuse the same identity/source."""

    vector_object = None
    _layer_id = "__loomstroke_comic_tools__"

    def __init__(
        self,
        layer_id: str,
        *,
        parent_id: str | None,
        attributes: Mapping[str, Any],
        position: int = -1,
        max_group_depth: int = 8,
    ) -> None:
        super().__init__()
        if (
            not layer_id
            or "id" in attributes
            or "parent_id" in attributes
            or "children" in attributes
        ):
            raise PanelFrameError("invalid prepared part insertion")
        values = {
            key: value.copy() if isinstance(value, QImage) else copy.deepcopy(value)
            for key, value in attributes.items()
        }
        self.layer_id = layer_id
        self.parent_id = parent_id
        self.position = position
        self.max_group_depth = max_group_depth
        self._layer = _Layer(id=layer_id, parent_id=parent_id, **values)
        if (
            self._layer.layer_type == "vector"
            and (self._layer.metadata or {}).get(PART_KEY, {}).get("role") == "edge"
        ):
            from loomstroke.plugins.vector_tools.common import (  # noqa: PLC0415
                ensure_vector_element_ids,
            )

            self._layer.vector_objects = [
                ensure_vector_element_ids(obj) for obj in self._layer.vector_objects
            ]
        self.last_error = ""

    def apply(self, canvas: Any) -> bool:
        query = canvas._layer_tree()

        def subtree_ids(node):
            yield node.id
            for child in node.children:
                yield from subtree_ids(child)

        incoming = tuple(subtree_ids(self._layer))
        if len(set(incoming)) != len(incoming) or any(
            query.find_layer_by_id_tree(layer_id) is not None for layer_id in incoming
        ):
            self.last_error = "prepared part identity already exists"
            return False
        parent = query.find_layer_by_id_tree(self.parent_id) if self.parent_id else None
        if self.parent_id and (parent is None or parent.layer_type != "group"):
            self.last_error = "prepared part parent is missing"
            return False

        def group_depth(node):
            return int(node.layer_type == "group") + max(
                (group_depth(child) for child in node.children), default=0
            )

        depth = group_depth(self._layer)
        node = parent
        seen = set()
        while node:
            if node.id in seen or node.locked:
                self.last_error = "prepared part parent is locked or cyclic"
                return False
            seen.add(node.id)
            depth += int(node.layer_type == "group")
            node = query.find_layer_by_id_tree(node.parent_id) if node.parent_id else None
        if depth > self.max_group_depth:
            self.last_error = "prepared part exceeds compositor group depth"
            return False
        siblings = parent.children if parent else canvas._layers
        index = len(siblings) if self.position < 0 else min(self.position, len(siblings))
        siblings.insert(index, self._layer)
        try:
            canvas._invalidate_layer_cache()
            sync_active_markers(canvas, LayerTreeQueryService(canvas))
        except Exception:
            siblings.remove(self._layer)
            canvas._invalidate_layer_cache()
            raise
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        location = canvas._layer_tree().find_parent(self.layer_id)
        if not self._executed or location is None or location[0][location[1]] is not self._layer:
            self.last_error = "prepared part cannot be removed"
            return False
        location[0].pop(location[1])
        canvas._invalidate_layer_cache()
        sync_active_markers(canvas, LayerTreeQueryService(canvas))
        self._executed = False
        return True
