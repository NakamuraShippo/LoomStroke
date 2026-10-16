"""Validate optional linked-frame references without trusting saved JSON shapes."""

from collections.abc import Mapping
from math import isfinite


def validated_story(value):
    if not isinstance(value, Mapping) or not all(
        isinstance(value.get(key), str) and value[key] for key in ("id", "member")
    ):
        return None
    frames = value.get("frames")
    if not isinstance(frames, list) or not frames:
        return None
    ids = set()
    for frame in frames:
        if not isinstance(frame, Mapping) or not isinstance(frame.get("id"), str):
            return None
        if not frame["id"] or frame["id"] in ids:
            return None
        ids.add(frame["id"])
        qualified = value.get("scope") != "document" or all(
            isinstance(frame.get(key), str) and frame[key]
            for key in ("page_id", "layer_id", "object_id")
        )
        if (
            not _valid_rect(frame.get("rect"))
            or not qualified
            or ("shape_frame" in frame and not isinstance(frame["shape_frame"], Mapping))
            or ("padding" in frame and not _valid_padding(frame["padding"]))
        ):
            return None
    return value if value["member"] in ids else None


def _valid_rect(rect):
    return (
        isinstance(rect, (list, tuple))
        and len(rect) == 4
        and all(
            not isinstance(x, bool) and isinstance(x, (int, float)) and isfinite(x) for x in rect
        )
        and rect[2] > 0
        and rect[3] > 0
    )


def _valid_padding(value):
    return (
        isinstance(value, (list, tuple))
        and len(value) == 4
        and all(
            not isinstance(x, bool) and isinstance(x, (int, float)) and isfinite(x) and x >= 0
            for x in value
        )
    )
