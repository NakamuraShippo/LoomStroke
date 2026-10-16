"""Wrap existing dialogue without recreating its text, runs, style or identity."""

import copy
import uuid
from dataclasses import replace

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import BALLOON_KEY, PART_KEY, TEXT_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError, validate_rect
from loomstroke.core.comic_panels.text_selection_command import PanelTextSelectionCommand
from loomstroke.core.command_types import BlendMode, LayerTransform
from loomstroke.plugins.text_tool.common import extract_text_payload

from .balloon_objects import build_balloon_object
from .model import BalloonRecord, BalloonTail
from .panel_balloon_create_plan import create_panel_balloon_command
from .panel_source_guard import source_guard
from .service import text_props_from_object
from .text_frames import TextBlockSize, autosize_body_rect


def _validate_source(state, part):
    seen = set()
    while part:
        if part.layer_id in seen:
            raise PanelFrameError("text source has a parent cycle")
        seen.add(part.layer_id)
        values = part.values
        if values["locked"] or not values["visible"] or values["mask_edit_mode"]:
            raise PanelFrameError("text source is hidden or locked")
        if (
            values["transform"] != LayerTransform.identity()
            or values["offset_x"]
            or values["offset_y"]
        ):
            raise PanelFrameError("finish the text source transform first")
        if values["clipping"] or values["effect_type"] or values["_preview_transform"]:
            raise PanelFrameError("finish the text source filter edit first")
        role = (values["metadata"] or {}).get(PART_KEY, {}).get("role")
        if values["layer_mask"] is not None and role != "content":
            raise PanelFrameError("text source has an independent mask")
        if values["opacity"] != 1:
            raise PanelFrameError("text source has inherited opacity")
        if values["blend_mode"] not in {BlendMode.NORMAL, BlendMode.PASS_THROUGH}:
            raise PanelFrameError("text source has an independent blend mode")
        parent_id = part.parent_id
        part = state.panel_parts.get(parent_id) if parent_id else None
        if parent_id and part is None:
            raise PanelFrameError("text source parent is missing")


def text_balloon_command(state, source_id, text_id, panel_id):
    source = state.panel_parts.get(source_id)
    record = state.panel_records.get(panel_id)
    frame = read_frame_layer(record.metadata) if record else None
    if source is None or frame is None:
        raise PanelFrameError("select text and a materialized panel")
    _validate_source(state, source)
    target = state.panel_parts[frame.panel_group_id]
    _validate_source(state, target)
    obj = next((o for o in source.values["vector_objects"] if o.id == text_id), None)
    payload = extract_text_payload(obj)
    if payload is None or TEXT_KEY in obj.meta or BALLOON_KEY in obj.meta:
        raise PanelFrameError("select ordinary text to wrap in a balloon")
    if payload.get("linked_story"):
        raise PanelFrameError("select a complete linked story before wrapping")
    box = payload.get("frame") or {}
    try:
        rect = tuple(float(box.get(k, 0)) for k in ("x", "y", "width", "height"))
    except (ValueError, TypeError) as exc:
        raise PanelFrameError("invalid text frame coordinates") from exc
    validate_rect(rect)
    props = text_props_from_object(obj)
    record = BalloonRecord(
        balloon_id=f"b{uuid.uuid4().hex[:8]}",
        panel_id=panel_id,
        body_rect=rect,
        text_object_id=obj.id,
        tail=BalloonTail(enabled=False),
        speaker_id=str(payload.get("speaker_id") or ""),
        speaker_name=str(payload.get("speaker_name") or ""),
    )
    body_rect = autosize_body_rect(
        record,
        props.text,
        font_size_pt=props.font_size_pt,
        vertical=props.vertical,
        line_height=props.line_height,
        measured_block=TextBlockSize(rect[2], rect[3], 1),
    )
    bx, by, bw, bh = body_rect
    record = replace(
        record,
        body_rect=body_rect,
        text_frame_rel=((rect[0] - bx) / bw, (rect[1] - by) / bh, rect[2] / bw, rect[3] / bh),
    )
    meta = copy.deepcopy(obj.meta)
    meta[TEXT_KEY] = {"schema_version": 1, "balloon_id": record.balloon_id, "panel_id": panel_id}
    text = replace(obj, meta=meta)
    create = create_panel_balloon_command(
        state, record, build_balloon_object(record), text, guarded=False
    )
    text_layer_id = create.commands[-1].layer_id
    remove = PanelLayerPatchCommand(
        source_id,
        before={"vector_objects": source.values["vector_objects"]},
        after={"vector_objects": [o for o in source.values["vector_objects"] if o.id != text_id]},
    )
    return (
        PanelEditCommand(
            [
                source_guard(state),
                remove,
                *create.commands,
                PanelTextSelectionCommand(text_id, text_layer_id),
            ],
            label="Wrap dialogue in panel balloon",
        ),
        record,
    )
