"""Remap rig root parameters onto a transformed crop's re-registered guide."""

from __future__ import annotations

from typing import Any

from loomstroke.core.canvas_geometry_service import CanvasGeometryService

from .raster_rig.contour_geometry import ContourGeometry
from .raster_rig.guide import build_attachment_guide
from .raster_rig_commit import png_b64_to_rgba


def transform_rig_pose(
    before: Any, after: dict[str, Any], mode: str, old_width: int, old_height: int
) -> None:
    renderer = after["renderer"]
    pose = renderer.get("pose")
    if not isinstance(pose, dict):
        raise ValueError("Cannot transform a rig balloon without its pose")
    body_before = png_b64_to_rgba(before["body_raster_png"])
    body_after = png_b64_to_rgba(after["body_raster_png"])
    if body_before is None or body_after is None:
        raise ValueError("Cannot transform a rig balloon without its body crop")
    old_guide = build_attachment_guide(body_before, tuple(before["body_rect"][:2]))
    new_guide = build_attachment_guide(body_after, tuple(after["body_rect"][:2]))
    if renderer.get("mode") == "contour_rebuild":
        old_guide = ContourGeometry.body_components(body_before, old_guide.origin)[0]
        new_guide = ContourGeometry.body_components(body_after, new_guide.origin)[0]
    root = old_guide.point_at(float(pose["root_u"]))
    # Guides describe pixel samples, unlike vector/text rectangle edges.
    # Map 0..width-1 then project onto the newly extracted guide.
    mapped_root = CanvasGeometryService.transform_point_for_canvas(
        root,
        mode=mode,
        old_w=body_before.shape[1] - 1,
        old_h=body_before.shape[0] - 1,
    )
    pose["root_u"] = new_guide.nearest_u(mapped_root)
    pose["tip"] = list(
        CanvasGeometryService.transform_point_for_canvas(
            pose["tip"],
            mode=mode,
            old_w=old_width - 1,
            old_h=old_height - 1,
        )
    )
    # Registration and inverse warping use pixel-sample coordinates for the
    # tip, just as for the root. Keep the nominal editing target in sync.
    after["tail"]["target"] = list(pose["tip"])
    if pose.get("bend") is not None and mode in {"flip_h", "flip_v"}:
        along, across = pose["bend"]
        pose["bend"] = [along, -across]
