"""Independent content-unit copies with stable identities and original pixels."""

from __future__ import annotations

import copy
import uuid
from dataclasses import fields, replace
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.command_types import Command

from .content_identity_metadata import remap_content_identity
from .membership import (
    BALLOON_KEY,
    FRONT_KEY,
    PART_KEY,
    SHARED_KEY,
    prepare_reparent_membership,
    subtree,
)
from .models import PanelFrameError
from .part_structure_command import PanelLayerInsertCommand

TEXT_LINK_KEY = "loomstroke:comic_balloon_text"


class PanelContentCopyCommand(Command):
    vector_object = None
    _layer_id = "__loomstroke_comic_tools__"

    def __init__(
        self,
        source_id: str,
        parent_id: str | None,
        *,
        expected_version: int,
        expected_parent_id: str | None,
        max_group_depth: int = 8,
        remove_shared_display: bool = False,
        front_panel_id: str | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__()
        self.source_id = source_id
        self.parent_id = parent_id
        self.expected_version = expected_version
        self.expected_parent_id = expected_parent_id
        self.max_group_depth = max_group_depth
        self.remove_shared_display = remove_shared_display
        self.front_panel_id = front_panel_id
        self.name = name
        self._namespace = uuid.uuid4()
        self.layer_id = self._id(source_id)
        self._insert: PanelLayerInsertCommand | None = None
        self._ever_applied = False
        self.last_error = ""

    def _id(self, value: str) -> str:
        return f"panel-copy-{uuid.uuid5(self._namespace, value).hex}"

    def _copy_values(self, node, balloon_ids, name):
        values = {}
        for field in fields(_Layer):
            key = field.name
            if key in {"id", "parent_id", "children", "vector_objects"}:
                continue
            value = getattr(node, key)
            if key.startswith("effect_cache") or key.startswith("_preview"):
                value = None if key in {"effect_cache", "_preview_transform"} else 0
            values[key] = value.copy() if isinstance(value, QImage) else copy.deepcopy(value)
        values["metadata"] = remap_content_identity(values.get("metadata"), balloon_ids)
        if (
            self.remove_shared_display
            and (values["metadata"] or {}).get(PART_KEY, {}).get("role") == "content_bundle"
        ):
            values["metadata"].pop(PART_KEY)
        if name is not None:
            values["name"] = name
        return values

    def _prepare(self, source: Any, query: Any) -> PanelLayerInsertCommand:
        nodes = list(subtree(source))
        object_ids = {obj.id for node in nodes for obj in node.vector_objects}
        layer_ids = {node.id for node in nodes}
        balloon_ids = {
            obj.meta[BALLOON_KEY]["balloon_id"]: self._id(obj.meta[BALLOON_KEY]["balloon_id"])
            for node in nodes
            for obj in node.vector_objects
            if BALLOON_KEY in obj.meta
        }
        object_map = {key: self._id(key) for key in object_ids}
        for node in nodes:
            for obj in node.vector_objects:
                balloon = obj.meta.get(BALLOON_KEY)
                if balloon:
                    object_map[obj.id] = f"comic-balloon-{self._id(balloon['balloon_id'])}"
        for node in nodes:
            marker = (node.metadata or {}).get(PART_KEY)
            shared_shell = (
                self.remove_shared_display
                and node.layer_type == "group"
                and marker
                and marker.get("role") == "content_bundle"
            )
            if (marker and not shared_shell) or node.ai_effect is not None:
                raise PanelFrameError(
                    "copy unit contains managed parts or external generation references"
                )
            for obj in node.vector_objects:
                story = (obj.meta.get("text_tool") or {}).get("linked_story")
                if story and (
                    story.get("scope") == "document"
                    or not {f.get("id") for f in story.get("frames", ())} <= object_ids
                ):
                    raise PanelFrameError(
                        "independent copy requires the complete same-page linked story"
                    )

        def clone(node, parent_id):
            values = self._copy_values(
                node, balloon_ids, self.name if node.id == source.id else None
            )
            objects = []
            for obj in node.vector_objects:
                meta = copy.deepcopy(obj.meta)
                meta["layer_id"] = self._id(node.id)
                for key in (BALLOON_KEY, TEXT_LINK_KEY):
                    payload = meta.get(key)
                    if payload:
                        for ref in ("balloon_id", "text_object_id"):
                            if payload.get(ref):
                                payload[ref] = (
                                    object_map[payload[ref]]
                                    if ref == "text_object_id" and payload[ref] in object_map
                                    else self._id(payload[ref])
                                )
                story = (meta.get("text_tool") or {}).get("linked_story")
                if story:
                    story["id"] = self._id(story["id"])
                    story["member"] = object_map[story["member"]]
                    for frame in story["frames"]:
                        frame["id"] = object_map[frame["id"]]
                        if frame.get("object_id") in object_ids:
                            frame["object_id"] = object_map[frame["object_id"]]
                        if frame.get("layer_id") in layer_ids:
                            frame["layer_id"] = self._id(frame["layer_id"])
                objects.append(replace(obj, id=object_map[obj.id], meta=meta))
            result = _Layer(
                id=self._id(node.id), parent_id=parent_id, vector_objects=objects, **values
            )
            result.children = [clone(c, result.id) for c in node.children]
            return result

        cloned = clone(source, self.parent_id)
        if self.remove_shared_display:
            self._strip_shared_display(cloned)
        for change in prepare_reparent_membership(query, cloned, self.parent_id):
            change.install()
        if self.front_panel_id is not None:
            self._install_front_owner(query, cloned)
        insert = PanelLayerInsertCommand(
            cloned.id,
            parent_id=self.parent_id,
            attributes={"name": cloned.name},
            max_group_depth=self.max_group_depth,
        )
        insert._layer = cloned
        return insert

    @staticmethod
    def _strip_shared_display(cloned):
        if SHARED_KEY not in (cloned.metadata or {}):
            raise PanelFrameError("copy source is not a shared display group")
        del cloned.metadata[SHARED_KEY]
        cloned.layer_mask = None

    def _install_front_owner(self, query, cloned):
        from .panel_gate import panel_groups  # noqa: PLC0415

        if self.front_panel_id not in panel_groups(query):
            raise PanelFrameError("copy page-front owner is missing")
        cloned.metadata[FRONT_KEY] = {"version": 1, "panel_id": self.front_panel_id}
        for node in subtree(cloned):
            objects = []
            for obj in node.vector_objects:
                meta = copy.deepcopy(obj.meta)
                for key in (BALLOON_KEY, TEXT_LINK_KEY):
                    if key in meta:
                        meta[key]["panel_id"] = self.front_panel_id
                objects.append(replace(obj, meta=meta))
            node.vector_objects = objects

    def apply(self, canvas: Any) -> bool:
        if self._insert is None:
            source = canvas._get_layer_by_id(self.source_id)
            if (
                source is None
                or source.texture_version != self.expected_version
                or source.parent_id != self.expected_parent_id
            ):
                self.last_error = "copy source changed during preparation"
                return False
            try:
                self._insert = self._prepare(source, canvas._layer_tree())
            except PanelFrameError as exc:
                self.last_error = str(exc)
                return False
        result = self._insert.apply(canvas)
        self.last_error = self._insert.last_error
        if not result and not self._ever_applied:
            self._insert = None
        self._ever_applied |= result
        self._executed = result
        return result

    def revert(self, canvas: Any) -> bool:
        if not self._executed or self._insert is None or not self._insert.revert(canvas):
            return False
        self._executed = False
        return True
