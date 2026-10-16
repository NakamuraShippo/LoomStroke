"""Owner gates for page-front content, with persisted original item flags."""

import copy

from .membership import FRONT_KEY, PART_KEY

GATE_KEY = "loomstroke.comictools.panel_gate"


def refresh_front_gates(canvas, frames, availability):
    query = canvas._layer_tree()
    items = [layer for layer in query.flatten_layers() if FRONT_KEY in (layer.metadata or {})]
    by_owner = {}
    for item in items:
        owner = item.metadata[FRONT_KEY].get("panel_id")
        if owner not in frames:
            continue  # The diagnostic/painting gate handles broken references.
        by_owner.setdefault(frames[owner].panel_group_id, []).append(item)
    changed = []
    for group_id, owned in by_owner.items():
        group = query.find_layer_by_id_tree(group_id)
        visible, locked = availability(query, group)
        metadata = copy.deepcopy(group.metadata or {})
        saved = metadata.get(GATE_KEY, {})
        for item in owned:
            original = saved.get(item.id)
            if original is None and (not visible or locked):
                original = {"visible": item.visible, "locked": item.locked}
                saved[item.id] = original
            if original is None:
                continue
            values = (bool(original["visible"] and visible), bool(original["locked"] or locked))
            if (item.visible, item.locked) != values:
                item.visible, item.locked = values
                changed.append(item.id)
            if visible and not locked:
                saved.pop(item.id)
        if saved:
            metadata[GATE_KEY] = saved
        else:
            metadata.pop(GATE_KEY, None)
        if metadata != group.metadata:
            group.metadata = metadata
            changed.append(group.id)
    if changed:
        canvas.bump_layer_texture_versions(changed)
        canvas._invalidate_layer_cache()


def set_managed_panel_lock(canvas, layer_id, locked):
    """Keep the existing property API; handle managed groups and nested parts."""
    query = canvas._layer_tree()
    layer = query.find_layer_by_id_tree(layer_id)
    if layer is None or PART_KEY not in (layer.metadata or {}):
        return False
    layer.locked = bool(locked)
    from .panel_gate import refresh_shared_masks  # noqa: PLC0415

    refresh_shared_masks(canvas)
    canvas._invalidate_layer_cache()
    canvas._recompose()
    return True
