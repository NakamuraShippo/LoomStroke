"""Prepare detached SAM layer pixels off-thread; apply existing tree commands on Qt."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from loomstroke.ai_selection.cut_to_layer_adapter import (
    _apply_extraction_mask,
    build_masked_layer_image,
)
from loomstroke.ai_selection.layer_mask_adapter import build_layer_mask_image_from_asset
from loomstroke.ai_selection.layer_mask_command_adapter import build_clipping_mask_apply_command
from loomstroke.core import raster_source_image
from loomstroke.core.command_layer_access import _layer_transform, _resolve_layer_by_id
from loomstroke.core.commands import AddLayerCommand, LayerTransform
from loomstroke.core.layer_mask_command_state import _MaskValue


class ExtractionPlacement:
    """Keep only detached geometry and masks for the worker's layer lookup."""

    def __init__(self, canvas: Any, layer: Any, source: SimpleNamespace) -> None:
        self.transform = _layer_transform(layer)
        self.requires_projection = self.transform != LayerTransform.translation(
            source.offset_x, source.offset_y
        )
        self.source = source
        self._layers: dict[str, tuple[SimpleNamespace, _MaskValue]] = {}
        if not self.requires_projection:
            return

        node = layer
        while node is not None:
            mask = _MaskValue.capture(node)
            snapshot = SimpleNamespace(
                parent_id=node.parent_id, layer_mask=mask.image, mask_enabled=mask.enabled
            )
            self._layers[node.id] = (snapshot, mask)
            node = _resolve_layer_by_id(canvas, node.parent_id) if node.parent_id else None

        self.source = self._layers[layer.id][0]
        self.source.image = source.image
        self.source.transform = self.transform

    def _find_layer_by_id_tree(self, layer_id: str) -> SimpleNamespace | None:
        entry = self._layers.get(layer_id)
        return entry[0] if entry is not None else None

    def matches(self, canvas: Any, layer: Any) -> bool:
        if _layer_transform(layer) != self.transform:
            return False
        for layer_id, (snapshot, mask) in self._layers.items():
            node = _resolve_layer_by_id(canvas, layer_id)
            if node is None or node.parent_id != snapshot.parent_id or not mask.matches(node):
                return False
        return True


class PreparedExtraction:
    """Small inner adapter; LayerEditCommand owns rollback and stable redo identities."""

    def __init__(
        self, position: tuple[int, int], frames: list[tuple[str, Any]], group_name: str | None
    ) -> None:
        self.position = position
        self.frames = frames
        self.group_name = group_name
        self.created_layer_ids: tuple[str, ...] = ()
        self.created_layer_id: str | None = None
        self.created_group_layer_id: str | None = None

    def apply(self, canvas: Any) -> bool:
        ids = []
        for name, image in self.frames:
            command = AddLayerCommand(name, layer_type="raster")
            if not command.apply(canvas):
                return False
            layer = canvas._get_layer_by_id(command._layer_id)
            if layer is None:
                return False
            layer.image = image
            layer.offset_x, layer.offset_y = self.position
            layer.texture_version += 1
            ids.append(layer.id)
        self.created_layer_ids = tuple(ids) if self.group_name is not None else ()
        if self.group_name is not None:
            self.created_group_layer_id = canvas.group_layers(ids, self.group_name)
            return bool(self.created_group_layer_id)
        self.created_layer_id = ids[0]
        return True


def prepare_layer_adapter(
    store: Any,
    source: Any,
    plan: dict[str, Any],
    width: int,
    height: int,
    placement: ExtractionPlacement | None = None,
) -> Any:
    common = {"canvas_width": width, "canvas_height": height}
    if plan["kind"] == "apply_clipping_mask":
        return build_clipping_mask_apply_command(
            store,
            plan["masks"][0][0],
            layer_id=source.id,
            mask_enabled=plan["maskEnabled"],
            **common,
        )
    placed = None
    position = (source.offset_x, source.offset_y)
    if placement is not None and placement.requires_projection:
        placed_layer = placement.source
        placed = raster_source_image.placed_source_image(
            placement, placed_layer, placed_layer.image, width, height
        )
        if placed is None:
            raise ValueError("The mask does not intersect the source layer.")
        position = (0, 0)

    frames = []
    for asset_id, name in plan["masks"]:
        mask = build_layer_mask_image_from_asset(store, asset_id, **common)
        if placed is not None:
            image = _apply_extraction_mask(placed, mask, mode=plan["mode"], require_visible=True)
        else:
            image = build_masked_layer_image(source, mask, mode=plan["mode"])
        if image is None:
            raise ValueError("The mask does not intersect the source layer.")
        frames.append((name, image))
    return PreparedExtraction(position, frames, plan.get("groupName"))
