"""Resolve the canonical content group and document bounds for panel tools."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from ..mask_coverage import mask_coverage_alpha8
from ..selection_shapes.selection_mask_bounds import SelectionMaskBounds
from .membership import PANEL_KEY, PART_KEY
from .metadata import read_frame_layer
from .models import PanelFrameError


@dataclass(frozen=True)
class PanelToolContext:
    panel_id: str
    content_group_id: str
    parent_id: str
    rect: tuple[int, int, int, int]


def panel_tool_context(canvas: Any, layer: Any) -> PanelToolContext | None:
    finder = getattr(canvas, "_find_layer_by_id_tree", None)
    if not callable(finder) or layer is None:
        return None
    node, content, seen = layer, None, set()
    while node is not None:
        layer_id = getattr(node, "id", None)
        if not isinstance(layer_id, str) or layer_id in seen:
            return None
        seen.add(layer_id)
        metadata = getattr(node, "metadata", None)
        marker = metadata.get(PART_KEY, {}) if isinstance(metadata, Mapping) else {}
        role = marker.get("role") if isinstance(marker, Mapping) else None
        if role == "content":
            content = node
        if role == "panel":
            if content is None:
                content = next(
                    (
                        child
                        for child in node.children
                        if (child.metadata or {}).get(PART_KEY, {}).get("role") == "content"
                    ),
                    None,
                )
            if content is None:
                return None
            return _from_carrier(node, content, layer)
        parent_id = getattr(node, "parent_id", None)
        node = finder(parent_id) if isinstance(parent_id, str) and parent_id else None
    return None


def _from_carrier(panel, content, source):
    for child in panel.children:
        for obj in getattr(child, "vector_objects", ()):
            record = obj.meta.get(PANEL_KEY)
            if not isinstance(record, Mapping):
                continue
            frame = read_frame_layer(record.get("metadata"))
            if frame is None or frame.content_group_id != content.id:
                continue
            rect = content_geometry_bounds(content)
            if rect is None:
                continue
            parent = source.id if source.layer_type == "group" else source.parent_id
            if source.id == panel.id:
                parent = content.id
            return PanelToolContext(str(record["panel_id"]), content.id, parent or content.id, rect)
    return None


def content_geometry_bounds(content):
    """Current content coverage is authoritative; hidden carriers have no paths."""
    mask = getattr(content, "layer_mask", None)
    if not isinstance(mask, QImage) or mask.isNull():
        return None
    alpha = mask_coverage_alpha8(mask)
    values = np.frombuffer(alpha.constBits(), np.uint8).reshape(
        alpha.height(), alpha.bytesPerLine()
    )
    return SelectionMaskBounds.find(values[:, : alpha.width()])


def frame_content_bounds(canvas, frame):
    finder = getattr(canvas, "_find_layer_by_id_tree", None)
    content = finder(frame.content_group_id) if callable(finder) else None
    return content_geometry_bounds(content)


def add_vector_in_panel(canvas, create, name, vector_kind):
    context = panel_tool_context(canvas, getattr(canvas, "active_layer", None))
    ref = create(name, layer_type="vector", vector_kind=vector_kind)
    if context is not None and not canvas.reparent_layer(ref.id, context.parent_id):
        canvas.remove_layer(ref.id)
        raise PanelFrameError("the new vector layer could not be placed in its panel")
    return ref
