"""Detached P1 conversion preparation, before public tool/UI wiring.

The current internal capability slice is a plain rectangular miter frame with
joined corners and pixel-aligned seams. Other capabilities retain their original vector;
they require the subsequent compositor/visual gate before enabling conversion.
"""

from __future__ import annotations

import copy
import json
import math
import uuid
from collections.abc import Mapping
from dataclasses import asdict, replace
from typing import Any

from loomstroke.core.comic_panels.clip_mask import union_clip_mask
from loomstroke.core.comic_panels.materialize_command import PanelFrameMaterializeCommand
from loomstroke.core.comic_panels.metadata import read_frame_layer, write_frame_layer
from loomstroke.core.comic_panels.models import (
    BoundaryRef,
    FrameEdge,
    FrameLayer,
    PanelFrameError,
    Rect,
    validate_rect,
)
from loomstroke.core.comic_panels.rect_edge_paths import rect_edge_paths
from loomstroke.plugins.vector_tools.common import (
    VECTOR_STROKE_PROFILE_META_KEY,
    Anchor,
    VectorObject,
    VectorPath,
    clone_vector_object_with_patch,
    ensure_vector_element_ids,
)

from .model import PANEL_META_KEY, PanelRecord, panel_record_from_object


def prepare_panel_frame_conversion(
    *,
    source_layer_id: str,
    source_object: Any,
    record: PanelRecord,
    rect: Rect,
    boundaries: Mapping[str, BoundaryRef],
    page_size: tuple[int, int],
    max_group_depth: int,
    layout_leaf_ids: tuple[str, ...] | None = None,
) -> PanelFrameMaterializeCommand:
    """Build a complete command without accessing or changing the live canvas."""
    validate_rect(rect)
    if panel_record_from_object(source_object) != record:
        raise PanelFrameError("source panel record changed")
    if read_frame_layer(record.metadata) is not None:
        raise PanelFrameError("panel already has a materialized frame")
    paths = source_object.paths
    if (
        len(paths) != 1
        or not paths[0].closed
        or len(paths[0].anchors) != 4
        or any(a.h_in or a.h_out for a in paths[0].anchors)
    ):
        raise PanelFrameError("frame conversion requires a straight rectangle")
    x, y, w, h = rect
    corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    if any(a.pos != p for a, p in zip(paths[0].anchors, corners, strict=True)):
        raise PanelFrameError("frame geometry differs from the layout rectangle")
    style = source_object.style
    width = style.stroke_width
    if (
        not math.isfinite(width)
        or width < 1
        or width >= min(w, h)
        or style.fill_enabled
        or style.dash
        or style.join != "miter"
        or not math.isfinite(style.miter_limit)
        or style.miter_limit < 2
        or VECTOR_STROKE_PROFILE_META_KEY in source_object.meta
        or source_object.preview
    ):
        raise PanelFrameError("frame style is outside the validated conversion slice")
    if set(boundaries) != {"top", "right", "bottom", "left"}:
        raise PanelFrameError("frame requires four boundary references")
    token = uuid.uuid4().hex
    group_id, content_id, carrier_id = (
        f"panel-{kind}-{token}" for kind in ("group", "content", "record")
    )
    edges, objects = [], []
    for side, points in rect_edge_paths(rect, width):
        edge_id = f"panel-edge-{side}-{token}"
        part_id = f"{edge_id}-layer"
        owned = (
            ("top_left", "top_right")
            if side == "top"
            else (("bottom_left", "bottom_right") if side == "bottom" else ())
        )
        edges.append(
            FrameEdge(
                edge_id,
                boundaries[side],
                "vector",
                part_id,
                owned_corners=owned,
                vector_restore_ref=edge_id,
            )
        )
        objects.append(
            ensure_vector_element_ids(
                VectorObject(
                    id=edge_id,
                    paths=[VectorPath(anchors=[Anchor(p) for p in points], closed=False)],
                    style=replace(style, cap="butt"),
                )
            )
        )
    frame = FrameLayer(
        layout_leaf_ids or (record.panel_id,),
        group_id,
        content_id,
        tuple(edges),
        clip_inset_px=0,
    )
    metadata = write_frame_layer(record.metadata, frame)
    metadata["frame_layer"]["vector_source"] = json.loads(
        json.dumps(
            {
                "paths": [asdict(p) for p in paths],
                "style": asdict(style),
            }
        )
    )
    carrier_meta = copy.deepcopy(source_object.meta)
    carrier_meta["layer_id"] = carrier_id
    carrier_meta[PANEL_META_KEY] = replace(record, metadata=metadata).to_meta()
    carrier = clone_vector_object_with_patch(source_object, paths=[], meta=carrier_meta)
    mask = union_clip_mask(page_size, [rect])
    return PanelFrameMaterializeCommand(
        source_layer_id=source_layer_id,
        source_object=source_object,
        carrier=carrier,
        frame=frame,
        edge_objects=tuple(objects),
        clip_mask=mask,
        carrier_layer_id=carrier_id,
        max_group_depth=max_group_depth,
    )
