"""Comic ownership updates prepared before ordinary layer-tree mutations.

Layer ownership is derived from ancestry. Only balloon records and explicitly
page-front items carry an owner reference; no second owner is added to images.
This adapter edits the existing metadata contract without importing plugins.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from .models import PanelFrameError

PART_KEY = "loomstroke.comictools.frame_part"
PANEL_KEY = "loomstroke:comic_panel"
BALLOON_KEY = "loomstroke:comic_balloon"
TEXT_KEY = "loomstroke:comic_balloon_text"
FRONT_KEY = "loomstroke.comictools.page_front"
SHARED_KEY = "loomstroke.comictools.shared_content"
UNIT_KEY = "loomstroke.comictools.balloon_unit"


def subtree(layer: Any):
    yield layer
    for child in getattr(layer, "children", ()):
        yield from subtree(child)


def _panel_for_group(group: Any) -> str:
    for child in getattr(group, "children", ()):
        marker = (getattr(child, "metadata", None) or {}).get(PART_KEY, {})
        if marker.get("role") != "carrier":
            continue
        for obj in getattr(child, "vector_objects", ()):
            payload = (getattr(obj, "meta", None) or {}).get(PANEL_KEY)
            if isinstance(payload, Mapping):
                if payload.get("schema_version") != 1:
                    raise PanelFrameError("unsupported panel membership version")
                owner = payload.get("panel_id")
                if isinstance(owner, str) and owner:
                    return owner
    raise PanelFrameError("panel group has no valid membership carrier")


def owner_for_parent(query: Any, parent_id: str | None) -> str:
    seen: set[str] = set()
    while parent_id:
        if parent_id in seen:
            raise PanelFrameError("cyclic panel membership ancestry")
        seen.add(parent_id)
        parent = query.find_layer_by_id_tree(parent_id)
        if parent is None:
            raise PanelFrameError("unresolved panel membership ancestry")
        marker = (getattr(parent, "metadata", None) or {}).get(PART_KEY, {})
        if marker.get("role") == "panel":
            return _panel_for_group(parent)
        parent_id = parent.parent_id
    return ""


def content_parent(query, parent_id):
    """Dropping on a logical panel row means its clipped contents."""
    parent = query.find_layer_by_id_tree(parent_id) if parent_id else None
    if parent and (parent.metadata or {}).get(PART_KEY, {}).get("role") == "panel":
        for child in parent.children:
            if (child.metadata or {}).get(PART_KEY, {}).get("role") == "content":
                return child.id
        raise PanelFrameError("panel has no content group")
    return parent_id


def whole_unit_source(query, layer_id):
    layer = query.find_layer_by_id_tree(layer_id)
    if layer and layer.parent_id:
        parent = query.find_layer_by_id_tree(layer.parent_id)
        if parent and UNIT_KEY in (parent.metadata or {}):
            return parent.id
    return layer_id


@dataclass
class MembershipChange:
    layer: Any
    before_objects: list[Any]
    after_objects: list[Any]
    before_metadata: dict[str, Any]
    after_metadata: dict[str, Any]

    def install(self, *, before: bool = False) -> None:
        self.layer.vector_objects = copy.deepcopy(
            self.before_objects if before else self.after_objects
        )
        self.layer.metadata = copy.deepcopy(self.before_metadata if before else self.after_metadata)


def prepare_reparent_membership(
    query: Any, layer: Any, new_parent_id: str | None
) -> tuple[MembershipChange, ...]:
    """Validate references and return detached edits; never mutate during planning."""
    marker = (getattr(layer, "metadata", None) or {}).get(PART_KEY, {})
    if marker.get("role") in {"content", "carrier", "edge", "restore"}:
        raise PanelFrameError("managed frame parts require a panel command")
    owner = owner_for_parent(query, new_parent_id)
    if (getattr(layer, "metadata", None) or {}).get(SHARED_KEY) and owner:
        raise PanelFrameError("shared content requires explicit independence")
    changes: list[MembershipChange] = []

    def visit(node: Any, current_owner: str) -> None:
        metadata = copy.deepcopy(getattr(node, "metadata", None) or {})
        node_marker = metadata.get(PART_KEY, {})
        if node_marker.get("role") == "panel":
            current_owner = _panel_for_group(node)
        after_metadata = copy.deepcopy(metadata)
        front = after_metadata.get(FRONT_KEY)
        if isinstance(front, Mapping):
            if front.get("version") != 1:
                raise PanelFrameError("unsupported page-front membership version")
            # An ordinary layer move leaves the explicit page-front plane. An
            # owner-only panel edit uses its dedicated metadata command instead.
            del after_metadata[FRONT_KEY]
        objects = list(getattr(node, "vector_objects", ()) or ())
        updated = []
        for obj in objects:
            original_meta = getattr(obj, "meta", None) or {}
            links = [
                key
                for key in (BALLOON_KEY, TEXT_KEY)
                if isinstance(original_meta.get(key), Mapping)
            ]
            if links:
                meta = copy.deepcopy(original_meta)
                for key in links:
                    link = dict(meta[key])
                    if link.get("schema_version") != 1:
                        raise PanelFrameError("unsupported balloon membership version")
                    link["panel_id"] = current_owner
                    meta[key] = link
                updated.append(replace(obj, meta=meta))
            else:
                updated.append(obj)
        if objects != updated or metadata != after_metadata:
            changes.append(
                MembershipChange(node, copy.deepcopy(objects), updated, metadata, after_metadata)
            )
        for child in getattr(node, "children", ()):
            visit(child, current_owner)

    visit(layer, owner)
    return tuple(changes)
