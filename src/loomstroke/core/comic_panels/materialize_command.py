"""Atomic construction of a frame with ordinary group/vector/mask layers.

No new renderer, public HostAPI method or persisted layer type is involved.
Geometry/mask preparation is detached; apply only installs prepared parts.
"""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.command_types import BlendMode, Command, LayerTransform
from loomstroke.core.layer_tree_service import LayerTreeQueryService, sync_active_markers

from .models import FrameLayer, PanelFrameError

PART_NAMESPACE = "loomstroke.comictools.frame_part"


class PanelFrameMaterializeCommand(Command):
    """Install one legacy rectangle's parts and a non-drawing record carrier.

    P1 internal path: unsupported source capabilities fail before any mutation.
    The caller supplies the compositor's actual group depth limit.
    """

    vector_object = None  # Existing generic command submission/history route.
    _layer_id = "__loomstroke_comic_tools__"

    def __init__(
        self,
        *,
        source_layer_id: str,
        source_object: Any,
        carrier: Any,
        frame: FrameLayer,
        edge_objects: tuple[Any, ...],
        clip_mask: QImage,
        carrier_layer_id: str,
        max_group_depth: int,
    ) -> None:
        super().__init__()
        if len(edge_objects) != len(frame.edges) or not frame.edges:
            raise PanelFrameError("frame parts do not match registry")
        if clip_mask.isNull() or max_group_depth < 2:
            raise PanelFrameError("invalid prepared frame capabilities")
        ids = [
            frame.panel_group_id,
            frame.content_group_id,
            carrier_layer_id,
            *(edge.part_layer_id for edge in frame.edges),
        ]
        if len(set(ids)) != len(ids) or source_layer_id in ids:
            raise PanelFrameError("duplicate materialized layer identity")
        self._source_id = source_layer_id
        self._expected = copy.deepcopy(source_object)
        self._carrier = copy.deepcopy(carrier)
        self._frame = frame
        self.panel_id = carrier.meta.get("loomstroke:comic_panel", {}).get(
            "panel_id", frame.layout_leaf_ids[0]
        )
        self._edges = copy.deepcopy(edge_objects)
        self._mask = clip_mask.copy()
        self._carrier_id = carrier_layer_id
        self._max_depth = max_group_depth
        self._created: _Layer | None = None
        self._before_objects: list[Any] | None = None
        self._before_active: str | None = None
        self._before_selection: Any = None
        self._source: Any = None
        self.last_error = ""

    def _validate_source(self, canvas: Any) -> tuple[Any, list[Any], int]:
        tree = canvas._layer_tree()
        source = tree.find_layer_by_id_tree(self._source_id)
        location = tree.find_parent(self._source_id)
        if source is None or location is None:
            raise PanelFrameError("source frame layer no longer exists")
        if (canvas._width, canvas._height) != (self._mask.width(), self._mask.height()):
            raise PanelFrameError("page dimensions changed during preparation")
        if source.layer_type != "vector" or source.vector_kind not in {"draw", "shape"}:
            raise PanelFrameError("source is not a frame vector layer")
        siblings, index = location
        if index + 1 < len(siblings) and siblings[index + 1].clipping:
            raise PanelFrameError("source is the base of a clipping chain")
        if source.opacity != 1.0 and len(source.vector_objects) > 1:
            raise PanelFrameError("shared frame layer opacity requires the visual gate")
        preview = getattr(canvas, "_vector_preview", None)
        if preview is not None and getattr(preview, "id", None) == self._expected.id:
            raise PanelFrameError("source frame has an unfinished vector edit")
        current = next((o for o in source.vector_objects if o.id == self._expected.id), None)
        if current != self._expected:
            raise PanelFrameError("source frame changed during preparation")
        for layer in tree.flatten_layers():
            if getattr(layer, "native_cmyk", None) is not None:
                raise PanelFrameError("native CMYK frame conversion is not validated")
        native = getattr(canvas, "_native_cmyk_service", None)
        if native is not None and native.profile() is not None:
            raise PanelFrameError("native CMYK frame conversion is not validated")
        self._validate_ancestry(source, tree)
        return source, *location

    def _validate_ancestry(self, source: Any, tree: Any) -> None:
        node = source
        depth = 0
        seen: set[str] = set()
        while node is not None:
            if node.id in seen:
                raise PanelFrameError("source has a cyclic parent chain")
            seen.add(node.id)
            if (
                node.locked
                or node.clipping
                or node.layer_mask is not None
                or node.transform != LayerTransform.identity()
                or node.offset_x
                or node.offset_y
                or getattr(node, "filter_type", None) is not None
                or getattr(node, "_preview_transform", None) is not None
            ):
                raise PanelFrameError("source or ancestor has unsupported frame capabilities")
            if node.blend_mode != BlendMode.NORMAL:
                raise PanelFrameError("source blend mode is not validated")
            parent_id = node.parent_id
            node = tree.find_layer_by_id_tree(parent_id) if parent_id else None
            if parent_id and node is None:
                raise PanelFrameError("source has an unresolved parent")
            if node is not None:
                depth += 1
        if depth + 2 > self._max_depth:
            raise PanelFrameError("frame conversion exceeds compositor group depth")

    def _prepare_group(self, source: Any) -> _Layer:
        group = _Layer(
            id=self._frame.panel_group_id,
            name=self._expected.id,
            layer_type="group",
            parent_id=source.parent_id,
            visible=source.visible,
            metadata={PART_NAMESPACE: {"version": 1, "role": "panel"}},
        )
        content = _Layer(
            id=self._frame.content_group_id,
            name=self._frame.content_group_id,
            layer_type="group",
            parent_id=group.id,
            layer_mask=self._mask.copy(),
            metadata={PART_NAMESPACE: {"version": 1, "role": "content"}},
        )
        carrier = _Layer(
            id=self._carrier_id,
            name=self._carrier_id,
            layer_type="vector",
            parent_id=group.id,
            locked=True,
            vector_objects=[self._carrier],
            metadata={PART_NAMESPACE: {"version": 1, "role": "carrier"}},
        )
        group.children = [content, carrier]
        for edge, obj in zip(self._frame.edges, self._edges, strict=True):
            # The legacy frame-layer opacity belongs to its ink. Applying it
            # to the new panel group would also fade newly inserted content
            # and introduce an extra quantization pass at antialiased edges.
            group.children.append(
                _Layer(
                    id=edge.part_layer_id,
                    name=edge.edge_id,
                    layer_type="vector",
                    parent_id=group.id,
                    opacity=source.opacity,
                    vector_objects=[obj],
                    metadata={
                        PART_NAMESPACE: {"version": 1, "role": "edge", "edge_id": edge.edge_id}
                    },
                )
            )
        return group

    def apply(self, canvas: Any) -> bool:
        self.last_error = ""
        try:
            source, siblings, index = self._validate_source(canvas)
            group = self._created or self._prepare_group(source)
            incoming = [group.id, *(layer.id for layer in group.children)]
            if any(canvas._get_layer_by_id(layer_id) is not None for layer_id in incoming):
                raise PanelFrameError("materialized layer identity already exists")
        except PanelFrameError as exc:
            self.last_error = str(exc)
            return False
        self._source = source
        self._before_objects = list(source.vector_objects)
        self._before_active = getattr(canvas, "_active_layer_id", None)
        self._before_selection = copy.deepcopy(getattr(canvas, "_vector_selection", None))
        self._created = group
        try:
            source.vector_objects = [o for o in source.vector_objects if o.id != self._expected.id]
            siblings.insert(index + 1, group)
            if (
                isinstance(self._before_selection, dict)
                and self._before_selection.get("id") == self._expected.id
            ):
                canvas._vector_selection = None
            canvas.bump_layer_texture_versions([source.id])
            canvas._invalidate_layer_cache()
            sync_active_markers(canvas, LayerTreeQueryService(canvas))
        except Exception:
            self._restore(canvas, siblings)
            raise
        self._executed = True
        return True

    def _restore(self, canvas: Any, siblings: list[Any]) -> None:
        siblings[:] = [layer for layer in siblings if layer is not self._created]
        self._source.vector_objects = list(self._before_objects or [])
        canvas._active_layer_id = self._before_active
        canvas._vector_selection = copy.deepcopy(self._before_selection)
        canvas.bump_layer_texture_versions([self._source.id])
        canvas._invalidate_layer_cache()
        sync_active_markers(canvas, LayerTreeQueryService(canvas))

    def revert(self, canvas: Any) -> bool:
        if not self._executed or self._created is None or self._before_objects is None:
            return False
        location = canvas._layer_tree().find_parent(self._created.id)
        if location is None or canvas._get_layer_by_id(self._source_id) is not self._source:
            self.last_error = "materialized frame cannot be restored"
            return False
        self._restore(canvas, location[0])
        self._executed = False
        return True
