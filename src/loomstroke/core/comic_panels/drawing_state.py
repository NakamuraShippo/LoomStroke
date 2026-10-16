"""Read effective drawing availability without touching document selection."""

from loomstroke.core.command_types import LayerTransform

from .drawing_target import DrawingTargetState
from .image_placement import PLACEMENT_KEY
from .membership import PART_KEY, owner_for_parent
from .pending_drawing import pending_drawing_layer


def drawing_states(canvas):
    query = canvas._layer_tree()
    result = {}
    for layer in query.flatten_layers():
        owner = owner_for_parent(query, layer.parent_id)
        if not owner:
            continue
        visible, locked, mask = layer.visible, layer.locked, layer.mask_edit_mode
        node, seen = layer, set()
        while node is not None and node.id not in seen:
            seen.add(node.id)
            visible &= node.visible
            locked |= node.locked
            mask |= node.mask_edit_mode
            node = query.find_layer_by_id_tree(node.parent_id) if node.parent_id else None
        metadata = layer.metadata or {}
        drawable = (
            layer.layer_type in {"raster", "vector"}
            and metadata.get(PART_KEY, {}).get("role") not in {"edge", "restore", "carrier"}
            and PLACEMENT_KEY not in metadata
            and layer.transform == LayerTransform.identity()
        )
        result[layer.id] = DrawingTargetState(layer.id, owner, drawable, visible, locked, mask)
    pending = pending_drawing_layer(canvas)
    if pending is not None:
        result[pending.id] = DrawingTargetState(
            pending.id, owner_for_parent(query, pending.parent_id)
        )
    return result


def panel_input_busy(canvas):
    return bool(
        getattr(canvas, "_in_transaction", False) or getattr(canvas, "_vector_preview", None)
    )
