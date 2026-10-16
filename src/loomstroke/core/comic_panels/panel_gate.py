"""Effective edit gates and derived shared coverage for ordinary panel groups."""

import numpy as np
from PySide6.QtGui import QImage

from loomstroke.core.command_types import LayerTransform
from loomstroke.core.mask_coverage import mask_coverage_alpha8

from .membership import FRONT_KEY, PANEL_KEY, PART_KEY, SHARED_KEY
from .metadata import read_frame_layer
from .shared_metadata import read_shared_content


def panel_groups(query):
    result = {}
    for layer in query.flatten_layers():
        if (layer.metadata or {}).get(PART_KEY, {}).get("role") != "carrier":
            continue
        for obj in layer.vector_objects:
            payload = obj.meta.get(PANEL_KEY)
            if payload:
                frame = read_frame_layer(payload.get("metadata"))
                if frame:
                    result[payload["panel_id"]] = frame
    return result


def availability(query, layer, _owner_seen=frozenset()):
    visible, locked = True, False
    seen = set()
    front = (getattr(layer, "metadata", None) or {}).get(FRONT_KEY) if layer else None
    while layer is not None:
        if layer.id in seen:
            return False, True
        seen.add(layer.id)
        front = front or (layer.metadata or {}).get(FRONT_KEY)
        visible &= layer.visible
        locked |= layer.locked or layer.mask_edit_mode
        parent_id = layer.parent_id
        layer = query.find_layer_by_id_tree(parent_id) if parent_id else None
        if parent_id and layer is None:
            return False, True
    if front:
        frame = panel_groups(query).get(front.get("panel_id"))
        if frame is None:
            return False, True
        owner = query.find_layer_by_id_tree(frame.panel_group_id)
        if owner is None or owner.id in _owner_seen:
            return False, True
        owner_visible, owner_locked = availability(query, owner, _owner_seen | {owner.id})
        visible &= owner_visible
        locked |= owner_locked
    return visible, locked


def panel_stroke_reason(canvas):  # noqa: PLR0911 - report distinct fail-closed target states
    if not callable(getattr(canvas, "_layer_tree", None)):
        return ""
    query = canvas._layer_tree()
    layer = canvas._get_active_layer()
    if layer is None:
        return ""
    node, seen, managed, shared, content = layer, set(), False, None, False
    while node is not None and node.id not in seen:
        seen.add(node.id)
        metadata = node.metadata or {}
        content |= metadata.get(PART_KEY, {}).get("role") == "content"
        managed |= PART_KEY in metadata or SHARED_KEY in metadata or FRONT_KEY in metadata
        if SHARED_KEY in metadata:
            shared = read_shared_content(metadata, group_id=node.id)
        node = query.find_layer_by_id_tree(node.parent_id) if node.parent_id else None
    if not managed:
        return ""
    visible, locked = availability(query, layer)
    if not visible or locked:
        return "panel drawing target is hidden or locked"
    placement_reason = _placement_reason(layer, content)
    if placement_reason:
        return placement_reason
    if shared:
        frames = panel_groups(query)
        active = False
        for panel_id in shared.panel_ids:
            frame = frames.get(panel_id)
            if frame is None:
                return "shared display target is missing"
            visible, locked = availability(query, query.find_layer_by_id_tree(frame.panel_group_id))
            if locked:
                return "shared source affects a locked panel"
            active |= visible
        if not active:
            return "all shared display targets are hidden"
    return ""


def _placement_reason(layer, content):
    if layer.layer_type != "raster" or layer.transform == LayerTransform.identity():
        return ""
    if not content:
        return "prepare the transformed frame edge for painting first"
    from ..raster_edit_geometry import RasterEditGeometry  # noqa: PLC0415

    try:
        RasterEditGeometry.capture(layer)
    except ValueError:
        return "raster placement is not ready for painting"
    return ""


def refresh_shared_masks(canvas):
    if not callable(getattr(canvas, "_layer_tree", None)):
        return
    query = canvas._layer_tree()
    from .front_gate import refresh_front_gates  # noqa: PLC0415

    frames = panel_groups(query)
    refresh_front_gates(canvas, frames, availability)
    sources = [layer for layer in query.flatten_layers() if SHARED_KEY in (layer.metadata or {})]
    if not sources:
        return
    prepared = []
    for source in sources:
        shared = read_shared_content(source.metadata, group_id=source.id)
        masks, key = [], []
        for panel_id in shared.panel_ids:
            frame = frames.get(panel_id)
            if frame is None:
                key.append((panel_id, None))
                continue
            group = query.find_layer_by_id_tree(frame.panel_group_id)
            content = query.find_layer_by_id_tree(frame.content_group_id)
            visible, _ = availability(query, group)
            mask = content.layer_mask if content is not None else None
            key.append((panel_id, visible, mask.cacheKey() if mask is not None else 0))
            if visible and mask is not None:
                masks.append(mask)
        key = tuple(key)
        if getattr(source, "_comic_shared_mask_key", None) == key:
            continue
        result = QImage(canvas._width, canvas._height, QImage.Format.Format_Alpha8)
        result.fill(0)
        pixels = np.frombuffer(result.bits(), np.uint8).reshape(
            result.height(), result.bytesPerLine()
        )
        for mask in masks:
            alpha = mask_coverage_alpha8(mask)
            data = np.frombuffer(alpha.constBits(), np.uint8).reshape(
                alpha.height(), alpha.bytesPerLine()
            )
            if alpha.size() != result.size():
                raise ValueError("shared mask dimensions differ from the page")
            np.maximum(
                pixels[:, : result.width()],
                data[:, : alpha.width()],
                out=pixels[:, : result.width()],
            )
        prepared.append((source, result, key))
    for source, result, key in prepared:
        if source.layer_mask != result:
            source.layer_mask = result
            canvas.bump_layer_texture_versions([source.id])
        source._comic_shared_mask_key = key
