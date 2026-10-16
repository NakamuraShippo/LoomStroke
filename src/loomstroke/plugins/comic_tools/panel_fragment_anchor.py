"""Derive a raster source anchor for a new display interval without resampling."""

import math

from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.models import PanelFrameError, RasterAnchor
from loomstroke.core.comic_panels.raster_source_origin import raster_image_anchor

from .panel_edge_geometry import segment_endpoints


def fragment_anchor(edge, part, rect, side, span, metadata):
    return anchor_at_endpoints(edge, part, segment_endpoints(rect, side, span), metadata)


def anchor_at_endpoints(edge, part, endpoints, metadata):
    effective, _ = raster_image_anchor(
        edge.raster_anchor,
        part.values["metadata"],
        part.values["transform"],
        (part.values["offset_x"], part.values["offset_y"]),
    )
    affine = part.values["transform"].to_affine()
    inverse = affine.inverse()
    if inverse is None:
        raise PanelFrameError("edge source transform is singular")
    a, b = endpoints
    px, py = inverse.apply((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    px, py = px - part.values["offset_x"], py - part.values["offset_y"]
    origin = metadata[PART_KEY].get("source_image_origin", [0.0, 0.0])
    basis = metadata[PART_KEY].get("paint_basis_origin")
    if basis:
        origin = (
            origin[0] + part.values["offset_x"] - basis[0],
            origin[1] + part.values["offset_y"] - basis[1],
        )
    sx, sy = math.cos(effective.source_angle_rad), math.sin(effective.source_angle_rad)
    scale = math.hypot(affine.a * sx + affine.b * sy, affine.c * sx + affine.d * sy)
    return RasterAnchor(
        (px + origin[0], py + origin[1]), 0.5, math.dist(a, b) / scale, effective.source_angle_rad
    )


def gap_anchor(first, second, source, other):
    anchors = []
    origins = []
    metadata = None
    for edge, part in ((first, source), (second, other)):
        anchor, marker = raster_image_anchor(
            edge.raster_anchor,
            part.values["metadata"],
            part.values["transform"],
            (part.values["offset_x"], part.values["offset_y"]),
        )
        anchors.append(anchor)
        origins.append(marker[PART_KEY].get("source_image_origin", (0, 0)))
        metadata = metadata or marker
    a, b = anchors
    if not math.isclose(a.source_angle_rad, b.source_angle_rad, abs_tol=1e-8):
        raise PanelFrameError("split raster sources have incompatible tangent bases")
    sx, sy = math.cos(a.source_angle_rad), math.sin(a.source_angle_rad)
    first_end = tuple(
        a.source_xy_px[i] + direction * (1 - a.edge_t) * a.base_edge_length_px
        for i, direction in enumerate((sx, sy))
    )
    second_start = tuple(
        b.source_xy_px[i]
        + origins[1][i]
        - origins[0][i]
        - direction * b.edge_t * b.base_edge_length_px
        for i, direction in enumerate((sx, sy))
    )
    midpoint = tuple((x + y) / 2 for x, y in zip(first_end, second_start, strict=True))
    effective = RasterAnchor(
        midpoint, 0.5, max(1, math.dist(first_end, second_start)), a.source_angle_rad
    )
    origin = metadata[PART_KEY].get("source_image_origin", (0, 0))
    registered = RasterAnchor(
        tuple(midpoint[i] + origin[i] for i in (0, 1)),
        0.5,
        effective.base_edge_length_px,
        a.source_angle_rad,
    )
    return registered, effective, metadata
