"""Copy text edit state and patch content without rebuilding saved typography."""

from __future__ import annotations

import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace

from loomstroke.core.japanese_text.annotations import ANNOTATION_KEYS, shift_annotation_ranges
from loomstroke.core.text_edit_delta import changed_text_span as _changed_span
from loomstroke.core.text_edit_ranges import shift_text_ranges as _shift_ranges
from loomstroke.plugins.vector_tools.common import VectorObject

from .common import (
    TEXT_META_KEY,
    TextToolSettings,
    ensure_text_vector_object,
    extract_text_content,
)


def clone_text_for_edit(obj: VectorObject) -> VectorObject:
    """Detach all mutable edit data from the object owned by the canvas/history."""
    return deepcopy(obj)


def replace_edited_text(
    obj: VectorObject,
    content: str,
    *,
    rect: tuple[float, float, float, float] | None = None,
    rotation_deg: float | None = None,
    cursor_position: int | None = None,
) -> VectorObject:
    """Return a detached text object with only content, ranges and geometry patched.

    ``cursor_position`` is a Python string index after the edit. It disambiguates
    repeated characters; callers converting Qt cursors must convert UTF-16 first.
    Transient preview metadata is omitted so callers can attach a fresh overlay.
    """
    result = clone_text_for_edit(obj)
    raw_payload = result.meta.get(TEXT_META_KEY)
    if not isinstance(raw_payload, Mapping):
        raise ValueError("Text editing requires text metadata")
    payload = dict(raw_payload)
    result.meta[TEXT_META_KEY] = payload
    payload.pop("inline_preview", None)
    previous = extract_text_content(payload)
    if content != previous:
        start, old_end, new_end = _changed_span(previous, content, cursor_position)
        span = {"start": start, "old_end": old_end, "new_end": new_end, "text_length": len(content)}
        for key in ("tracking_runs", "line_height_runs"):
            if key in payload:
                payload[key] = _shift_ranges(payload[key], **span)
        document = payload.get("document")
        if isinstance(document, Mapping):
            document = dict(document)
            document["text"] = content
            for key in ("runs", "paragraphs"):
                if key in document:
                    document[key] = _shift_ranges(document[key], **span)
            for key in ANNOTATION_KEYS:
                if key in document:
                    document[key] = shift_annotation_ranges(
                        document[key],
                        kind=key,
                        start=start,
                        old_end=old_end,
                        new_end=new_end,
                        text=content,
                    )
            payload["document"] = document
        payload["content"] = content
        payload["updated_at"] = time.time()
    if rect is not None or rotation_deg is not None:
        result = _replace_geometry(result, payload, rect, rotation_deg)
    return result


def _replace_geometry(obj, payload, rect, rotation_deg):
    frame = payload.get("frame")
    frame = dict(frame) if isinstance(frame, Mapping) else {}
    transform = payload.get("transform")
    transform = dict(transform) if isinstance(transform, Mapping) else {}
    old_rect = tuple(
        float(frame.get(key, payload.get(key, default)))
        for key, default in (("x", 0.0), ("y", 0.0), ("width", 1.0), ("height", 1.0))
    )
    old_rotation = float(transform.get("rotation_deg", payload.get("rotation_deg", 0.0)))
    target_rect = rect if rect is not None else old_rect
    angle = rotation_deg if rotation_deg is not None else old_rotation
    if target_rect == old_rect and angle == old_rotation:
        return obj
    geometry = ensure_text_vector_object(
        object_id=obj.id,
        content=extract_text_content(payload),
        rect=target_rect,
        settings=TextToolSettings(),
        rotation_deg=angle,
    )
    for key, value in zip(("x", "y", "width", "height"), target_rect, strict=True):
        frame[key] = float(value)
        payload[key] = float(value)
    payload["frame"] = frame
    transform["rotation_deg"] = float(angle)
    payload["transform"] = transform
    payload["rotation_deg"] = float(angle)
    payload["updated_at"] = time.time()
    return replace(obj, paths=geometry.paths)
