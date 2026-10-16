"""Rebuild derived masks after viewport changes without trimming ink sources."""

from .clip_mask import union_clip_mask
from .convex_clip import inset_polygon, polygon_clip_mask
from .convex_edges import edge_ownership_masks, span_ownership_mask
from .derived_clip import authored_coverage
from .edge_span_mask import span_window
from .membership import PART_KEY
from .models import PanelFrameError
from .owner_page_geometry import owner_page_geometry


def frame_crop_masks(frame, carrier, polygons, layers, size, source_size):
    polygon = owner_page_geometry(polygons, frame.layout_leaf_ids)
    shapes = [polygon.after_points]
    xs, ys = zip(*(p for points in shapes for p in points), strict=True)
    rect = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    inset = frame.clip_inset_px
    if frame.geometry_mode == "layout_tree_rect":
        x, y, w, h = rect
        content = union_clip_mask(size, [(x + inset, y + inset, w - 2 * inset, h - 2 * inset)])
    else:
        content = polygon_clip_mask(size, [inset_polygon(p, inset) for p in shapes])
    source_content = layers[frame.content_group_id]
    old_points = polygon.before_points
    if frame.geometry_mode == "layout_tree_rect":
        ox, oy = min(p[0] for p in old_points), min(p[1] for p in old_points)
        ow, oh = max(p[0] for p in old_points) - ox, max(p[1] for p in old_points) - oy
        expected = union_clip_mask(
            source_size, [(ox + inset, oy + inset, ow - 2 * inset, oh - 2 * inset)]
        )
    else:
        expected = polygon_clip_mask(source_size, [inset_polygon(old_points, inset)])
    authored = authored_coverage(
        metadata=source_content.metadata or {},
        enabled=source_content.mask_enabled,
        mask=source_content.layer_mask,
        frame=frame,
        expected=expected,
    )
    result = {} if authored else {frame.content_group_id: content}
    ownership = None
    for index, edge in enumerate(frame.edges):
        part = layers.get(edge.part_layer_id)
        if part is None:
            raise PanelFrameError("frame crop source part is missing")
        marker = (part.metadata or {}).get(PART_KEY, {})
        if marker.get("nearest_mask"):
            if ownership is None:
                owners = [None] * len(polygon.after_points)
                for e in frame.edges:
                    position = polygon.after_boundaries.index(e.boundary_ref)
                    for corner in e.owned_corners:
                        owners[int(corner[7:])] = position
                if None in owners:
                    raise PanelFrameError("convex crop corner ownership is missing")
                ownership = edge_ownership_masks(
                    size,
                    polygon.after_points,
                    owners,
                    corner_radius=carrier.style.stroke_width * carrier.style.miter_limit / 2 + 2,
                )
            position = polygon.after_boundaries.index(edge.boundary_ref)
            a, b = (
                polygon.after_points[position],
                polygon.after_points[(position + 1) % len(polygon.after_points)],
            )
            result[part.id] = span_ownership_mask(ownership[position], a, b, edge.span)
        elif marker.get("span_window"):
            side = edge.side or ("top", "right", "bottom", "left")[index]
            result[part.id] = span_window(
                size,
                rect,
                side,
                edge.span,
                window_ends=marker.get("window_ends", (False, False)),
                width=marker.get("window_width", 0),
            )
    return result
