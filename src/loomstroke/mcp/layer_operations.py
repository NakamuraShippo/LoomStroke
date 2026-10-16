"""Layer operation validation and mutations executed inside LayerEditCommand."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.core.commands import BlendMode, LayerTransform
from loomstroke.core.mask_coverage import coverage_to_mask_image

from .editing_common import invalid
from .types import McpStructuredError, ReasonCode

PROPERTY_FIELDS = {
    "visible": "visible",
    "locked": "locked",
    "alphaLock": "alpha_lock",
    "clipping": "clipping",
}
MASK_ACTIONS = {"add", "remove", "from_selection", "from_asset", "enable", "edit"}


def _conflict(message: str) -> McpStructuredError:
    return McpStructuredError(ReasonCode.CONFLICT, message)


def _layer(canvas: Any, layer_id: Any) -> Any:
    if not isinstance(layer_id, str) or not layer_id:
        raise invalid("'layerId' must be a non-empty string.", "layerId")
    layer = canvas._get_layer_by_id(layer_id)
    if layer is None:
        raise McpStructuredError(
            ReasonCode.NOT_FOUND, "Layer not found.", details={"layerId": layer_id}
        )
    return layer


def _editable(canvas: Any, layer: Any, *, allow_locked: bool = False) -> None:
    if layer.locked and not allow_locked:
        raise _conflict("Unlock the layer before editing it.")
    parent_id = layer.parent_id
    while parent_id is not None:
        parent = _layer(canvas, parent_id)
        if parent.locked:
            raise _conflict("Unlock the parent group before editing its contents.")
        parent_id = parent.parent_id


def _siblings(canvas: Any, layer: Any) -> list[Any]:
    if layer.parent_id is None:
        roots = canvas.get_layer_tree_snapshot().root_layer_ids
        return [_layer(canvas, layer_id) for layer_id in roots]
    return _layer(canvas, layer.parent_id).children


def _protect_background(canvas: Any, layer: Any) -> None:
    roots = canvas.get_layer_tree_snapshot().root_layer_ids
    if roots and layer.id == roots[0]:
        raise _conflict("The background layer cannot be removed or moved into a group.")


def _validate_group(canvas: Any, args: Mapping[str, Any]) -> list[str]:
    ids = args.get("layerIds")
    if (
        not isinstance(ids, list)
        or len(ids) < 2
        or any(not isinstance(item, str) or not item for item in ids)
    ):
        raise invalid("'layerIds' must contain at least two layer IDs.", "layerIds")
    if len(set(ids)) != len(ids):
        raise invalid("'layerIds' must not contain duplicates.", "layerIds")
    layers = [_layer(canvas, item) for item in ids]
    if len({layer.parent_id for layer in layers}) != 1:
        raise _conflict("Layers must share a parent before grouping.")
    for layer in layers:
        _editable(canvas, layer)
        _protect_background(canvas, layer)
    name = args.get("name", "Group")
    if not isinstance(name, str) or not name.strip():
        raise invalid("'name' must be a non-empty string.", "name")
    return ids


def _validate_visible(canvas: Any, args: Mapping[str, Any]) -> list[str]:
    if "layerId" in args or "targetLayerId" in args:
        raise invalid("Visible stamping does not accept layer IDs.")
    visible = canvas._merge_flatten()._collect_visible_content_layers()
    if len(visible) < 2:
        raise _conflict("At least two visible content layers are required.")
    return []


def _validate_update(canvas: Any, layer: Any, args: Mapping[str, Any]) -> None:
    properties = args.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        raise invalid("'properties' must be a non-empty object.", "properties")
    if set(properties) - (PROPERTY_FIELDS.keys() | {"active"}):
        raise invalid("Unsupported layer property.", "properties")
    if any(not isinstance(value, bool) for value in properties.values()):
        raise invalid("Layer properties must be booleans.", "properties")
    if "active" in properties and properties["active"] is not True:
        raise invalid("Use 'active': true to select a layer.", "properties.active")
    if set(properties) != {"active"}:
        _editable(canvas, layer, allow_locked=True)


def _validate_move(canvas: Any, layer: Any, args: Mapping[str, Any]) -> None:
    parent_id = args.get("parentId", layer.parent_id)
    position = args.get("position")
    if isinstance(position, bool) or not isinstance(position, int) or position < 0:
        raise invalid("'position' must be a non-negative integer.", "position")
    parent = _layer(canvas, parent_id) if parent_id is not None else None
    if parent is not None:
        _editable(canvas, parent)
        if parent.layer_type != "group" and not (
            layer.layer_type == "effect" and parent.layer_type in {"raster", "vector"}
        ):
            raise invalid(
                "The destination must be a group (or a content layer for a filter).",
                "parentId",
            )
        cursor = parent
        while cursor is not None:
            if cursor.id == layer.id:
                raise _conflict("A layer cannot be moved into its own subtree.")
            cursor = _layer(canvas, cursor.parent_id) if cursor.parent_id else None
    _protect_background(canvas, layer)
    roots = canvas.get_layer_tree_snapshot().root_layer_ids
    siblings = parent.children if parent is not None else [_layer(canvas, key) for key in roots]
    max_position = len(siblings) - int(layer in siblings)
    if position > max_position or (parent is None and position == 0):
        raise invalid("'position' is outside the available destination positions.", "position")


def _validate_mask(
    canvas: Any, layer: Any, args: Mapping[str, Any], mask_image: QImage | None
) -> None:
    action = args.get("action")
    if not isinstance(action, str) or action not in MASK_ACTIONS:
        raise invalid("Unsupported mask action.", "action")
    if getattr(canvas, "_mask_paint_dirty", False):
        raise _conflict("Finish the current mask stroke before changing masks.")
    if action in {"enable", "edit"}:
        if not isinstance(args.get("enabled"), bool):
            raise invalid("'enabled' must be a boolean.", "enabled")
        if layer.layer_mask is None:
            raise _conflict("The layer has no attached mask.")
    if action == "from_selection" and canvas.get_selection_mask() is None:
        raise _conflict("A selection is required to create a mask.")
    if action == "from_asset":
        if mask_image is None or mask_image.isNull():
            raise invalid("A valid raster 'assetId' is required.", "assetId")
        state = canvas.get_canvas_state_snapshot()
        if (mask_image.width(), mask_image.height()) != (state.width, state.height):
            raise invalid("The mask asset must match the canvas dimensions.", "assetId")
    if action not in {"enable", "edit"} and "enabled" in args:
        raise invalid("'enabled' is only accepted by enable and edit actions.", "enabled")
    if action != "from_asset" and "assetId" in args:
        raise invalid("'assetId' is only accepted by from_asset.", "assetId")


def _validate_merge(canvas: Any, layer: Any, args: Mapping[str, Any]) -> list[str]:
    if args.get("mode", "down") != "down":
        raise invalid("'mode' must be down or visible.", "mode")
    _protect_background(canvas, layer)
    siblings = _siblings(canvas, layer)
    index = siblings.index(layer)
    if index == 0:
        raise _conflict("There is no layer below the source.")
    target = siblings[index - 1]
    if args.get("targetLayerId", target.id) != target.id:
        raise _conflict("Merge-down requires the immediately preceding sibling layer.")
    _editable(canvas, target)
    if layer.layer_type not in {
        "raster",
        "vector",
        "group",
        "mask",
        "3d",
    } or target.layer_type not in {"raster", "vector", "group", "mask"}:
        raise invalid("The source and destination must contain paintable content.")
    if not layer.visible or not target.visible:
        raise _conflict("Show both layers before merging them.")
    return [layer.id, target.id]


def validate(
    canvas: Any, kind: str, args: Mapping[str, Any], mask_image: QImage | None = None
) -> list[str]:
    if kind == "group_layers":
        return _validate_group(canvas, args)
    if kind == "merge_layers" and args.get("mode") == "visible":
        return _validate_visible(canvas, args)
    layer = _layer(canvas, args.get("layerId"))
    if kind == "update_layer":
        _validate_update(canvas, layer, args)
        return [layer.id]
    _editable(canvas, layer)
    if kind == "move_layer":
        _validate_move(canvas, layer, args)
    elif kind == "set_layer_mask":
        _validate_mask(canvas, layer, args, mask_image)
    elif kind == "merge_layers":
        return _validate_merge(canvas, layer, args)
    elif kind in {"delete_layer", "ungroup_layer"}:
        _protect_background(canvas, layer)
        if kind == "ungroup_layer" and layer.layer_type != "group":
            raise invalid("The layer must be a group.", "layerId")
        if kind == "delete_layer" and len(canvas.get_layer_tree_snapshot().root_layer_ids) <= 1:
            raise _conflict("The last root layer cannot be deleted.")
    elif kind == "clear_layer" and layer.layer_type not in {"raster", "vector", "mask"}:
        raise invalid("Only raster, vector, and mask layer contents can be cleared.", "layerId")
    elif kind == "rasterize_layer" and layer.layer_type != "vector":
        raise invalid("The layer must be a vector layer.", "layerId")
    return [layer.id]


def _group(canvas: Any, ids: list[str], args: Mapping[str, Any]) -> dict[str, Any]:
    parent_id = _layer(canvas, ids[0]).parent_id
    group_id = canvas.group_layers(ids, args.get("name", "Group"))
    if group_id is None:
        raise _conflict("The layers could not be grouped.")
    _layer(canvas, group_id).parent_id = parent_id
    return {"groupId": group_id, "layerIds": ids}


def _visible(canvas: Any) -> dict[str, Any]:
    compositor = canvas._merge_flatten()
    image = compositor.composite_visible(compositor._collect_visible_content_layers())
    layer_id = canvas.add_layer("Merged").id
    _layer(canvas, layer_id).image = image
    return {"layerId": layer_id, "mode": "visible", "originalsPreserved": True}


def perform(
    canvas: Any, kind: str, args: Mapping[str, Any], mask_image: QImage | None = None
) -> dict[str, Any]:
    ids = validate(canvas, kind, args, mask_image)
    if kind == "group_layers":
        return _group(canvas, ids, args)
    if kind == "merge_layers" and args.get("mode") == "visible":
        return _visible(canvas)
    if kind == "merge_layers":
        return _merge(canvas, _layer(canvas, ids[0]), _layer(canvas, ids[1]))
    layer = _layer(canvas, ids[0])
    simple = {
        "move_layer": lambda: canvas.reparent_layer(
            layer.id, args.get("parentId", layer.parent_id), args["position"]
        ),
        "ungroup_layer": lambda: canvas.ungroup_layer(layer.id),
        "delete_layer": lambda: _remove(canvas, layer),
        "rasterize_layer": lambda: _rasterize(canvas, layer),
    }
    if kind in simple:
        if not simple[kind]():
            raise _conflict("The layer operation could not be applied.")
    elif kind == "update_layer":
        for key, value in args["properties"].items():
            if key == "active":
                canvas.set_active_layer(layer.id)
            else:
                setattr(layer, PROPERTY_FIELDS[key], value)
    elif kind == "clear_layer":
        previous_id = canvas.current_layer().id
        canvas.set_active_layer(layer.id)
        canvas.clear_layer()
        canvas.set_active_layer(previous_id)
    elif kind == "set_layer_mask":
        _mask(canvas, layer, args, mask_image)
    return {"layerId": layer.id}


def _merge(canvas: Any, layer: Any, target: Any) -> dict[str, Any]:
    # Reuse the compositor's vector/effect/mask/transform/clipping semantics,
    # then keep the destination identity and its placement in the tree.
    bottom = copy.copy(target)
    bottom.clipping = False
    image = canvas._merge_flatten().composite_visible([(0, bottom), (1, layer)])
    if not _remove(canvas, layer):
        raise _conflict("The merge source could not be removed.")
    _cleanup_detached(canvas, [node for child in target.children for node in _subtree(child)])
    target.image = image
    target.layer_type = "raster"
    target.vector_objects = []
    target.children = []
    target.layer_mask = None
    target.mask_edit_mode = False
    target.offset_x = target.offset_y = 0
    target.transform = LayerTransform.identity()
    target._preview_transform = None
    target.opacity = 1.0
    target.blend_mode = BlendMode.NORMAL
    canvas.set_active_layer(target.id)
    return {"layerId": target.id, "removedLayerId": layer.id, "mode": "down"}


def _rasterize(canvas: Any, layer: Any) -> bool:
    offset = layer.offset_x, layer.offset_y
    if not canvas.rasterize_layer(layer.id):
        return False
    # The existing rasterizer draws geometry without applying layer placement.
    # Preserve that placement so conversion leaves the rendered image in place.
    layer.offset_x, layer.offset_y = offset
    return True


def _subtree(layer: Any) -> list[Any]:
    return [layer, *(node for child in layer.children for node in _subtree(child))]


def _remove(canvas: Any, layer: Any) -> bool:
    nodes = _subtree(layer)
    if not canvas.remove_layer(layer.id):
        return False
    _cleanup_detached(canvas, nodes)
    return True


def _cleanup_detached(canvas: Any, nodes: list[Any]) -> None:
    registry = getattr(canvas, "_reference_registry", None)
    ids = {node.id for node in nodes}
    for node in nodes:
        canvas._cascade_ai_effect_refs_on_delete(node.id)
        if registry is not None:
            registry.unregister_referrer(node.id)
    selection = getattr(canvas, "_vector_selection", None)
    if isinstance(selection, Mapping) and selection.get("layer_id") in ids:
        canvas._vector_selection = None
    if getattr(canvas, "_vector_preview_layer_id", None) in ids:
        canvas._vector_preview = canvas._vector_preview_layer_id = None


def _mask(canvas: Any, layer: Any, args: Mapping[str, Any], mask_image: QImage | None) -> None:
    action = args["action"]
    if action == "add":
        if not canvas.add_layer_mask(layer.id):
            raise _conflict("The mask could not be added.")
    elif action == "remove":
        canvas.remove_layer_mask(layer.id)
        layer.mask_edit_mode = False
    elif action == "from_selection":
        if not canvas.selection_to_layer_mask(layer.id):
            raise _conflict("The selection could not be converted to a mask.")
    elif action == "from_asset":
        layer.layer_mask = coverage_to_mask_image(mask_image)
        layer.mask_enabled = True
    elif action == "enable":
        canvas.set_layer_mask_enabled(layer.id, args["enabled"])
    elif action == "edit":
        canvas.set_mask_edit_mode(layer.id, args["enabled"])
