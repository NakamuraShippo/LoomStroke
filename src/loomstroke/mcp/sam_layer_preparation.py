"""Prepare detached SAM layer pixels off-thread; apply existing tree commands on Qt."""

from __future__ import annotations

from typing import Any

from loomstroke.ai_selection.cut_to_layer_adapter import build_masked_layer_image
from loomstroke.ai_selection.layer_mask_adapter import build_layer_mask_image_from_asset
from loomstroke.ai_selection.layer_mask_command_adapter import build_clipping_mask_apply_command
from loomstroke.core.commands import AddLayerCommand


class PreparedExtraction:
    """Small inner adapter; LayerEditCommand owns rollback and stable redo identities."""

    def __init__(self, source: Any, frames: list[tuple[str, Any]], group_name: str | None) -> None:
        self.source = source
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
            layer.offset_x = self.source.offset_x
            layer.offset_y = self.source.offset_y
            layer.texture_version += 1
            ids.append(layer.id)
        self.created_layer_ids = tuple(ids) if self.group_name is not None else ()
        if self.group_name is not None:
            self.created_group_layer_id = canvas.group_layers(ids, self.group_name)
            return bool(self.created_group_layer_id)
        self.created_layer_id = ids[0]
        return True


def prepare_layer_adapter(
    store: Any, source: Any, plan: dict[str, Any], width: int, height: int
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
    frames = []
    for asset_id, name in plan["masks"]:
        mask = build_layer_mask_image_from_asset(store, asset_id, **common)
        image = build_masked_layer_image(source, mask, mode=plan["mode"])
        if image is None:
            raise ValueError("The mask does not intersect the source layer.")
        frames.append((name, image))
    return PreparedExtraction(source, frames, plan.get("groupName"))
