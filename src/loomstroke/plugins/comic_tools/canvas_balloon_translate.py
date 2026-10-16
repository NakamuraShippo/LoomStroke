"""Translate balloon edit records without resampling any embedded source."""

import copy
import math

from .canvas_balloon_transform import _update_parametric_metadata
from .model import BALLOON_META_KEY, BALLOON_META_SCHEMA_VERSION


def translate_balloon_metadata(metadata, dx, dy):
    result = copy.deepcopy(metadata)
    record = result[BALLOON_META_KEY]
    if record.get("schema_version", 1) != BALLOON_META_SCHEMA_VERSION or not all(
        math.isfinite(v) for v in (dx, dy)
    ):
        raise ValueError("Cannot translate an unsupported comic balloon record")
    for key in ("body_rect", "tail_rect"):
        rect = record.get(key)
        if rect is not None:
            rect[:2] = [rect[0] + dx, rect[1] + dy]
    tail = record.get("tail")
    if tail and tail.get("target") is not None:
        tail["target"] = [tail["target"][0] + dx, tail["target"][1] + dy]
    renderer = record.get("renderer")
    if renderer and renderer.get("kind") == "raster_rig":
        pose = renderer["pose"]
        pose["tip"] = [pose["tip"][0] + dx, pose["tip"][1] + dy]
    _update_parametric_metadata(result, record)
    return result
