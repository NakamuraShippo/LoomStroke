"""Patch an existing balloon text object without rebuilding saved typography."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from loomstroke.plugins.text_tool.editing_metadata import replace_edited_text
from loomstroke.plugins.vector_tools.common import VectorObject

from .model import BALLOON_TEXT_META_KEY, BalloonRecord
from .text_frames import balloon_text_rect


def update_existing_balloon_text(
    previous: VectorObject,
    record: BalloonRecord,
    content: str,
    *,
    previous_record: BalloonRecord | None = None,
    property_changes: Mapping[str, Any] | None = None,
) -> VectorObject:
    """Reuse text range rules; change frame geometry only when the balloon does.

    The existing object's rotation and extension metadata remain authoritative.
    Canvas managed transforms/resampling have already updated their own records;
    this helper never applies another scale or a text-schema conversion.
    """
    rect = balloon_text_rect(record)
    changed_frame = previous_record is None or rect != balloon_text_rect(previous_record)
    result = replace_edited_text(previous, str(content or ""), rect=rect if changed_frame else None)
    result = apply_balloon_text_properties(result, property_changes)
    marker = result.meta.get(BALLOON_TEXT_META_KEY)
    link = dict(marker) if isinstance(marker, Mapping) else {"schema_version": 1}
    link.update(balloon_id=record.balloon_id, panel_id=record.panel_id)
    result.meta[BALLOON_TEXT_META_KEY] = link
    return result


def apply_balloon_text_properties(
    obj: VectorObject, changes: Mapping[str, Any] | None
) -> VectorObject:
    """Apply only properties explicitly changed by the balloon editor."""
    if not changes:
        return obj
    from loomstroke.plugins.text_tool.properties_controller import (  # noqa: PLC0415
        replace_text_property,
    )

    for key, value in changes.items():
        obj = replace_text_property(obj, key, list(value) if key == "color_rgba" else value)
    return obj
