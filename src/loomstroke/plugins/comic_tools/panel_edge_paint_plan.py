"""Bake an explicitly chosen raster edge for normal canvas paint/erase editing.

This is a bitmap editing operation, using Qt's existing image transform
primitive; document rendering remains exclusively the wgpu pipeline.
"""

from __future__ import annotations

import copy
import math
import uuid
from dataclasses import replace

from PySide6.QtGui import QImage, QPainter, QTransform

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer, write_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError, RasterAnchor
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand
from loomstroke.core.comic_panels.raster_patch_command import PanelRasterPatchCommand
from loomstroke.core.comic_panels.raster_source_origin import raster_image_anchor
from loomstroke.core.command_types import LayerTransform

from .model import PANEL_META_KEY
from .panel_source_guard import source_guard


def bake_edge_for_paint_command(state, panel_id, edge_id):
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    edge = next(e for e in frame.edges if e.edge_id == edge_id)
    part = state.panel_parts[edge.part_layer_id]
    if edge.render_mode != "raster" or part.values["locked"]:
        raise PanelFrameError("paint preparation requires an editable raster edge")
    if part.values["transform"] == LayerTransform.identity():
        return None
    image = part.values["image"]
    anchor, metadata = raster_image_anchor(
        edge.raster_anchor,
        part.values["metadata"],
        part.values["transform"],
        (part.values["offset_x"], part.values["offset_y"]),
    )
    affine = part.values["transform"].to_affine()
    ox, oy = part.values["offset_x"], part.values["offset_y"]
    points = [
        affine.apply(x + ox, y + oy)
        for x, y in (
            (0, 0),
            (image.width(), 0),
            (image.width(), image.height()),
            (0, image.height()),
        )
    ]
    left, top = math.floor(min(p[0] for p in points)), math.floor(min(p[1] for p in points))
    right, bottom = math.ceil(max(p[0] for p in points)), math.ceil(max(p[1] for p in points))
    integer_translation = (
        affine.a == affine.d == 1
        and affine.b == affine.c == 0
        and affine.tx == round(affine.tx)
        and affine.ty == round(affine.ty)
    )
    if integer_translation:
        baked = image.copy()
    else:
        baked = QImage(right - left, bottom - top, QImage.Format.Format_ARGB32_Premultiplied)
        baked.fill(0)
        painter = QPainter(baked)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setTransform(
            QTransform(
                affine.a,
                affine.c,
                affine.b,
                affine.d,
                affine.tx + affine.a * ox + affine.b * oy - left,
                affine.ty + affine.c * ox + affine.d * oy - top,
            )
        )
        painter.drawImage(0, 0, image)
        painter.end()
    px, py = affine.apply(anchor.source_xy_px[0] + ox, anchor.source_xy_px[1] + oy)
    sx, sy = math.cos(anchor.source_angle_rad), math.sin(anchor.source_angle_rad)
    dx, dy = affine.a * sx + affine.b * sy, affine.c * sx + affine.d * sy
    baked_anchor = RasterAnchor(
        (px - left, py - top),
        anchor.edge_t,
        anchor.base_edge_length_px * math.hypot(dx, dy),
        math.atan2(dy, dx),
    )
    metadata[PART_KEY].update(source_image_origin=[0.0, 0.0], paint_basis_origin=[left, top])
    updates = {
        "image": baked,
        "offset_x": left,
        "offset_y": top,
        "transform": LayerTransform.identity(),
        "metadata": metadata,
    }
    patch = PanelRasterPatchCommand(
        part.layer_id, before={key: part.values[key] for key in updates}, after=updates
    )
    restore_id = f"edge-paint-source-{uuid.uuid4().hex}"
    backup = PanelLayerInsertCommand(
        restore_id,
        parent_id=frame.panel_group_id,
        attributes={
            "name": edge.edge_id,
            "layer_type": "raster",
            "image": image,
            "offset_x": ox,
            "offset_y": oy,
            "transform": part.values["transform"],
            "visible": False,
            "locked": True,
            "metadata": {PART_KEY: {"version": 1, "role": "restore", "edge_id": edge.edge_id}},
        },
    )
    changed = replace(
        frame,
        edges=tuple(
            replace(e, raster_anchor=baked_anchor) if e.edge_id == edge_id else e
            for e in frame.edges
        ),
    )
    layer_id, carrier = state.panel_sources[panel_id]
    carrier_meta = copy.deepcopy(carrier.meta)
    frame_metadata = write_frame_layer(state.panel_records[panel_id].metadata, changed)
    for payload in frame_metadata["frame_layer"]["edges"]:
        if payload["edge_id"] == edge_id:
            payload.setdefault("paint_source_layer_ids", []).append(restore_id)
    carrier_meta[PANEL_META_KEY]["metadata"] = frame_metadata
    carrier_patch = PanelLayerPatchCommand(
        layer_id,
        before={"vector_objects": state.panel_parts[layer_id].values["vector_objects"]},
        after={"vector_objects": [replace(carrier, meta=carrier_meta)]},
    )
    return PanelEditCommand(
        [source_guard(state), backup, patch, carrier_patch], label="Prepare frame edge for painting"
    )
