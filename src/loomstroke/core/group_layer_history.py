"""Own one reversible group and only its child placements across history restores."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .events import Events
from .layer_history_memory import estimate_retained_layer_bytes

logger = logging.getLogger(__name__)


@dataclass
class _Change:
    roots: list[Any]
    siblings: list[Any]
    children: list[Any]
    positions: list[int]
    group_index: int
    old_children: list[Any]
    old_parent: str | None
    child_parents: list[str | None]
    active_id: str | None
    active_index: int


class GroupLayerHistory:
    """Long-lived custody excludes sibling lists and unrelated layer objects."""

    def __init__(self) -> None:
        self.group: Any | None = None
        self.child_ids: tuple[str, ...] = ()
        self.positions: tuple[int, ...] = ()
        self.parent_id: str | None = None
        self.group_index = 0
        self.active: dict[bool, str | None] = {}

    @staticmethod
    def supports(canvas: Any) -> bool:
        return (
            isinstance(getattr(canvas, "_layers", None), list)
            and callable(getattr(canvas, "_layer_tree", None))
            and callable(getattr(canvas, "_create_group_layer_for_tree_service", None))
            and callable(getattr(canvas, "_find_parent", None))
        )

    @staticmethod
    def _active(canvas: Any) -> str | None:
        return canvas.current_layer().id or None

    @staticmethod
    def _refresh(canvas: Any) -> None:
        from .layer_tree_service import LayerTreeQueryService, sync_active_markers  # noqa: PLC0415

        sync_active_markers(canvas, LayerTreeQueryService(canvas))
        canvas._invalidate_layer_cache()
        canvas._recompose()

    @staticmethod
    def _owner(canvas: Any, siblings: list[Any]) -> str | None:
        if siblings is canvas._layers:
            return None
        return next(
            layer.id
            for layer in canvas._layer_tree().flatten_layers()
            if layer.children is siblings
        )

    def _siblings(self, canvas: Any) -> list[Any] | None:
        if self.parent_id is None:
            return canvas._layers
        parent = canvas._layer_tree().find_layer_by_id_tree(self.parent_id)
        return parent.children if parent is not None and parent.layer_type == "group" else None

    def prepare_group(self, canvas: Any, ids: list[str], name: str | None) -> bool:
        try:
            if len(ids) < 2 or len(set(ids)) != len(ids):
                return False
            locations = [canvas._find_parent(identifier) for identifier in ids]
            if any(location is None for location in locations):
                return False
            siblings = locations[0][0]
            if any(location[0] is not siblings for location in locations):
                return False
            positions = sorted(location[1] for location in locations)
            children = [siblings[position] for position in positions]
            query = canvas._layer_tree()
            removed_groups = sum(
                layer.layer_type == "group" for layer in query.flatten_layers(children)
            )
            group_count = sum(layer.layer_type == "group" for layer in query.flatten_layers())
            group = canvas._create_group_layer_for_tree_service(
                name or f"Group {group_count - removed_groups + 1}"
            )
            if query.find_layer_by_id_tree(group.id) is not None:
                return False
            self.parent_id = self._owner(canvas, siblings)
            group.parent_id = self.parent_id
            self.group = group
            self.child_ids = tuple(child.id for child in children)
            self.positions, self.group_index = tuple(positions), min(positions)
            self.active = {False: self._active(canvas), True: self._active(canvas)}
            return True
        except Exception as exc:
            logger.warning("Group preparation failed (%s)", type(exc).__name__)
            return False

    def prepare_ungroup(self, canvas: Any, identifier: str) -> bool:
        group = canvas._layer_tree().find_layer_by_id_tree(identifier)
        location = canvas._find_parent(identifier)
        if group is None or group.layer_type != "group" or location is None:
            return False
        ids = tuple(child.id for child in group.children)
        if len(set(ids)) != len(ids):
            return False
        siblings, index = location
        self.parent_id = self._owner(canvas, siblings)
        self.group, self.child_ids, self.group_index = group, ids, index
        self.positions = tuple(range(index, index + len(ids)))
        self.active = {True: self._active(canvas)}
        return True

    def _capture(self, canvas: Any, grouped: bool) -> _Change | None:
        siblings = self._siblings(canvas)
        if self.group is None or siblings is None:
            return None
        live = canvas._layer_tree().find_layer_by_id_tree(self.group.id)
        if grouped:
            if live is not None:
                return None
            by_id = {layer.id: index for index, layer in enumerate(siblings)}
            if any(identifier not in by_id for identifier in self.child_ids):
                return None
            positions = [by_id[identifier] for identifier in self.child_ids]
            children = [siblings[position] for position in positions]
            group_index = self.group_index
        else:
            location = canvas._find_parent(self.group.id)
            if live is None or location is None or location[0] is not siblings:
                return None
            if tuple(child.id for child in live.children) != self.child_ids:
                return None
            # A page snapshot may reconstruct the same logical group as a new object.
            self.group = live
            children, positions, group_index = list(live.children), [], location[1]
        return _Change(
            canvas._layers,
            siblings,
            children,
            positions,
            group_index,
            self.group.children,
            self.group.parent_id,
            [child.parent_id for child in children],
            getattr(canvas, "_active_layer_id", None),
            canvas._active_index,
        )

    def _install(self, change: _Change, grouped: bool) -> None:
        group, siblings = self.group, change.siblings
        if grouped:
            for position in sorted(change.positions, reverse=True):
                siblings.pop(position)
            group.children = change.children
            for child in change.children:
                child.parent_id = group.id
            siblings.insert(min(self.group_index, len(siblings)), group)
        else:
            siblings.pop(change.group_index)
            for position, child in zip(self.positions, change.children, strict=True):
                child.parent_id = self.parent_id
                siblings.insert(min(position, len(siblings)), child)
            # The live children are now owned by the document, not this detached group.
            group.children = []
        group.parent_id = self.parent_id

    def _same_context(self, canvas: Any, change: _Change) -> bool:
        return canvas._layers is change.roots and self._siblings(canvas) is change.siblings

    def _matches(self, canvas: Any, change: _Change, grouped: bool) -> bool:
        live = canvas._layer_tree().find_layer_by_id_tree(self.group.id)
        if grouped:
            return (
                live is self.group
                and any(layer is self.group for layer in change.siblings)
                and self.group.parent_id == self.parent_id
                and len(self.group.children) == len(change.children)
                and all(a is b for a, b in zip(self.group.children, change.children, strict=True))
                and all(child.parent_id == self.group.id for child in change.children)
            )
        present = {id(layer) for layer in change.siblings}
        return (
            live is None
            and not self.group.children
            and all(
                id(child) in present and child.parent_id == self.parent_id
                for child in change.children
            )
        )

    @staticmethod
    def _pop_owned(siblings: list[Any], layer: Any) -> None:
        index = next(index for index, candidate in enumerate(siblings) if candidate is layer)
        siblings.pop(index)

    def _rollback(self, canvas: Any, change: _Change, grouped: bool) -> None:
        if not self._same_context(canvas, change) or not self._matches(canvas, change, grouped):
            logger.warning("Group rollback refused after ownership changed")
            return
        if grouped:
            self._pop_owned(change.siblings, self.group)
            for position, child in sorted(zip(change.positions, change.children, strict=True)):
                change.siblings.insert(min(position, len(change.siblings)), child)
        else:
            owned = {id(child) for child in change.children}
            indices = [i for i, layer in enumerate(change.siblings) if id(layer) in owned]
            for index in reversed(indices):
                change.siblings.pop(index)
            change.siblings.insert(min(change.group_index, len(change.siblings)), self.group)
        self.group.children, self.group.parent_id = change.old_children, change.old_parent
        for child, parent_id in zip(change.children, change.child_parents, strict=True):
            child.parent_id = parent_id
        canvas._active_layer_id, canvas._active_index = change.active_id, change.active_index
        self._refresh(canvas)

    def set_grouped(self, canvas: Any, grouped: bool) -> bool:
        change = self._capture(canvas, grouped)
        if change is None:
            return False
        try:
            self._install(change, grouped)
            if grouped in self.active:
                canvas._active_layer_id = self.active[grouped]
            self._refresh(canvas)
            if not self._same_context(canvas, change) or not self._matches(canvas, change, grouped):
                return False
            canvas._publish_layer_event(
                Events.LAYER_CHANGED,
                layer=self.group,
                change_type="grouped" if grouped else "ungrouped",
            )
            success = self._same_context(canvas, change) and self._matches(canvas, change, grouped)
            if success:
                self.active.setdefault(grouped, self._active(canvas))
            return success
        except Exception as exc:
            try:
                self._rollback(canvas, change, grouped)
            except Exception as rollback_exc:
                logger.warning("Group rollback refresh failed (%s)", type(rollback_exc).__name__)
            logger.warning("Group transition failed (%s)", type(exc).__name__)
            return False

    @property
    def retained_bytes(self) -> int:
        return estimate_retained_layer_bytes(self.group)
