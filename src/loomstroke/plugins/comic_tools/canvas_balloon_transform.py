"""Reorient balloon edit records and their embedded crops, without schema changes."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

import numpy as np

from loomstroke.core.canvas_geometry_service import CanvasGeometryService

from .model import BALLOON_META_KEY, BALLOON_META_SCHEMA_VERSION


def transform_balloon_metadata(
    metadata: Mapping[str, Any], mode: str, old_width: int, old_height: int
) -> dict[str, Any] | None:
    payload = metadata.get(BALLOON_META_KEY)
    if not isinstance(payload, Mapping):
        return None
    if mode.startswith("translate:"):
        from .canvas_balloon_translate import translate_balloon_metadata  # noqa: PLC0415

        _, dx, dy = mode.split(":")
        return translate_balloon_metadata(metadata, float(dx), float(dy))
    if mode not in {"flip_h", "flip_v", "flip_hv", "rot90", "rot-90", "rot180"}:
        return None
    if payload.get("schema_version", 1) != BALLOON_META_SCHEMA_VERSION:
        raise ValueError("Cannot transform an unsupported comic balloon record")
    result = copy.deepcopy(dict(metadata))
    record = result[BALLOON_META_KEY]

    def point(value, width=old_width, height=old_height):
        return list(
            CanvasGeometryService.transform_point_for_canvas(
                value,
                mode=mode,
                old_w=width,
                old_h=height,
            )
        )

    def rect(value, width=old_width, height=old_height):
        return list(
            CanvasGeometryService.transform_bbox_for_canvas(
                *value,
                mode=mode,
                old_w=width,
                old_h=height,
            )
        )

    record["body_rect"] = rect(record["body_rect"])
    if record.get("tail_rect") is not None:
        record["tail_rect"] = rect(record["tail_rect"])
    tail = record.get("tail")
    if isinstance(tail, dict) and tail.get("target") is not None:
        tail["target"] = point(tail["target"])
    for key in ("body_outline", "tail_outline"):
        if record.get(key):
            record[key] = [point(p, 1, 1) for p in record[key]]
    if record.get("text_frame_rel") is not None:
        record["text_frame_rel"] = rect(record["text_frame_rel"], 1, 1)

    _transform_crops(record, mode, point)
    _transform_axis_offsets(record, mode)
    renderer = record.get("renderer")
    if isinstance(renderer, dict) and renderer.get("kind") == "raster_rig":
        from .canvas_rig_transform import transform_rig_pose  # noqa: PLC0415

        transform_rig_pose(payload, record, mode, old_width, old_height)
    _update_parametric_metadata(result, record)
    return result


def _transform_crops(record, mode, point):
    from .raster_rig_commit import png_b64_to_rgba, rgba_to_png_b64  # noqa: PLC0415

    for key in ("body_raster_png", "tail_raster_png"):
        if not record.get(key):
            continue
        image = png_b64_to_rgba(record[key])
        if image is None:
            raise ValueError("Cannot transform an unreadable balloon crop")
        height, width = image.shape[:2]
        if key == "tail_raster_png":
            for anchor in ("tail_local_root", "tail_local_tip"):
                if record.get(anchor) is not None:
                    record[anchor] = point(record[anchor], width, height)
            if record.get("tail_local_base") is not None:
                record["tail_local_base"] = [
                    point(p, width, height) for p in record["tail_local_base"]
                ]
        if mode == "flip_h":
            image = image[:, ::-1]
        elif mode == "flip_v":
            image = image[::-1]
        else:
            turns = {"flip_hv": 2, "rot180": 2, "rot90": -1, "rot-90": 1}[mode]
            image = np.rot90(image, turns)
        record[key] = rgba_to_png_b64(image)


def _transform_axis_offsets(record, mode):
    # Across-axis offsets change handedness under a reflection, not a rotation.
    reflected = mode in {"flip_h", "flip_v"}
    if reflected:
        if record.get("tail_bend") is not None:
            along, across = record["tail_bend"]
            record["tail_bend"] = [along, -across]
        if record.get("tail_root_corner_offsets") is not None:
            record["tail_root_corner_offsets"] = [
                [along, -across] for along, across in record["tail_root_corner_offsets"]
            ]


def _update_parametric_metadata(meta: dict[str, Any], record: dict[str, Any]) -> None:
    shape = meta.get("shape_manager_parametric")
    if not isinstance(shape, dict):
        return
    from loomstroke.plugins.vector_tools.parametric_shapes import (  # noqa: PLC0415
        tail_params_for_tip,
    )

    x, y, w, h = record["body_rect"]
    bbox = (x, y, x + w, y + h)
    shape["reference_bbox"] = list(bbox)
    tail = record.get("tail") or {}
    if tail.get("enabled", True) and tail.get("target") is not None:
        params = dict(shape.get("params") or {})
        params.update(tail_params_for_tip(bbox, tail["target"]))
        shape["params"] = params
