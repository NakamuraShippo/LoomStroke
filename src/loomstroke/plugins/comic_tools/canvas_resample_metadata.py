"""Uniform Image Size transforms for comic edit records (no schema changes)."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Any

from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.resample_metadata import scale_frame_metadata
from loomstroke.core.document_snapshot_builder import DocumentSnapshotBuilder
from loomstroke.core.image_size.image_size_planner import ImageSizePlanner
from loomstroke.core.image_size.raster_resample_service import RasterResampleService

from .layout_tree import layout_from_payload
from .model import BALLOON_META_KEY, LAYOUT_LAYER_NAMESPACE, PANEL_META_KEY
from .raster_rig.guide import build_attachment_guide
from .raster_rig_commit import png_b64_to_rgba, rgba_to_png_b64


def resample_comic_metadata(meta: Any, sx: float, sy: float, mode: Any) -> dict | None:
    plugins = meta.get("plugins")
    layout = plugins.get(LAYOUT_LAYER_NAMESPACE) if isinstance(plugins, Mapping) else None
    balloon = meta.get(BALLOON_META_KEY)
    if layout is None and balloon is None and PANEL_META_KEY not in meta and PART_KEY not in meta:
        return None
    if not math.isclose(sx, sy, rel_tol=1e-12, abs_tol=0):
        raise ValueError("Comic Tools currently requires uniform Image Size scaling")
    result = scale_frame_metadata(meta, sx) or copy.deepcopy(meta)
    if layout is not None:
        if (
            not isinstance(layout, Mapping)
            or layout.get("schema_version", 1) != 1
            or layout_from_payload(layout) is None
        ):
            raise ValueError("Cannot resample an invalid comic layout")
        payload = result["plugins"][LAYOUT_LAYER_NAMESPACE]
        from .layout_geometry_space import scaled_space  # noqa: PLC0415

        scaled_space(payload.setdefault("metadata", {}), (1, 1), sx)
        parsed = layout_from_payload(layout)
        for name in ("gutter_px", "margin_px"):
            payload[name] = float(getattr(parsed, name)) * sx
    if balloon is not None:
        _scale_balloon(result[BALLOON_META_KEY], sx, mode)
    return result


def _scale_balloon(  # noqa: PLR0912, PLR0915 - one branch per stored geometry field
    record: dict, factor: float, mode: Any
) -> None:
    if not isinstance(record, Mapping) or record.get("schema_version", 1) != 1:
        raise ValueError("Cannot resample an unsupported balloon record")
    renderer = record.get("renderer")
    if renderer and (
        not isinstance(renderer, Mapping)
        or renderer.get("kind") != "raster_rig"
        or renderer.get("version", 1) != 1
    ):
        raise ValueError("Cannot resample an unsupported balloon renderer")
    body_before = None
    root = None
    if renderer:
        body_before = png_b64_to_rgba(record["body_raster_png"])
        if body_before is None:
            raise ValueError("Cannot read rig body crop")
        guide = build_attachment_guide(body_before, tuple(record["body_rect"][:2]))
        root = guide.point_at(float(renderer["pose"]["root_u"]))
    for name in ("body_rect", "tail_rect"):
        if record.get(name) is not None:
            record[name] = [float(value) * factor for value in record[name]]
    for name in ("padding_px", "tail_base_gap"):
        if name in record:
            record[name] = float(record[name]) * factor
    record["tail"]["target"] = [float(v) * factor for v in record["tail"]["target"]]
    if record.get("tail_bend") is not None:
        along, across = record["tail_bend"]
        record["tail_bend"] = [along, across * factor]
    if record.get("tail_root_corner_offsets") is not None:
        record["tail_root_corner_offsets"] = [
            [float(v) * factor for v in pair] for pair in record["tail_root_corner_offsets"]
        ]
    for prefix in ("body", "tail"):
        key = f"{prefix}_raster_png"
        if not record.get(key):
            continue
        rgba = png_b64_to_rgba(record[key])
        if rgba is None:
            raise ValueError("Cannot read balloon crop")
        height, width = rgba.shape[:2]
        tw = max(1, ImageSizePlanner.round_half_up(width * factor))
        th = max(1, ImageSizePlanner.round_half_up(height * factor))
        image = DocumentSnapshotBuilder.array_to_qimage_native_size(rgba)
        scaled = RasterResampleService().resample_premultiplied(image, tw, th, mode)
        record[key] = rgba_to_png_b64(DocumentSnapshotBuilder.qimage_to_array(scaled))
        record[f"{prefix}_rect"][2:] = [tw, th]
        if prefix == "tail":

            def point(p, fx=tw / width, fy=th / height):
                return [(p[0] + 0.5) * fx - 0.5, (p[1] + 0.5) * fy - 0.5]

            for name in ("tail_local_root", "tail_local_tip"):
                if record.get(name) is not None:
                    record[name] = point(record[name])
            if record.get("tail_local_base") is not None:
                record["tail_local_base"] = [point(p) for p in record["tail_local_base"]]
    if renderer:
        after = png_b64_to_rgba(record["body_raster_png"])
        guide = build_attachment_guide(after, tuple(record["body_rect"][:2]))
        mapped = (
            (root[0] + 0.5) * after.shape[1] / body_before.shape[1] - 0.5,
            (root[1] + 0.5) * after.shape[0] / body_before.shape[0] - 0.5,
        )
        pose = renderer["pose"]
        pose["root_u"] = guide.nearest_u(mapped)
        pose["tip"] = [(float(v) + 0.5) * factor - 0.5 for v in pose["tip"]]
        record["tail"]["target"] = list(pose["tip"])
        if pose.get("bend") is not None:
            pose["bend"] = [pose["bend"][0], pose["bend"][1] * factor]
        for name in ("inset_px", "overlap_px"):
            if renderer.get(name) is not None:
                renderer[name] *= factor
