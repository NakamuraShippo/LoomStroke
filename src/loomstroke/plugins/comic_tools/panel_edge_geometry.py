"""Rectangle edge segments preserve side identity across split and merge."""

from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.rect_edge_paths import rect_edge_paths

from .panel_resize_plan import edge_endpoints

SIDES = ("top", "right", "bottom", "left")


def registered_endpoints(state, panel_id, edge, index):
    polygon = state.panel_polygons.get(panel_id)
    if polygon is None:
        return segment_endpoints(state.panel_rects[panel_id], edge_side(edge, index), edge.span)
    try:
        position = polygon.boundaries.index(edge.boundary_ref)
    except ValueError as exc:
        raise PanelFrameError("edge boundary no longer exists in the polygon") from exc
    a, b = polygon.points[position], polygon.points[(position + 1) % len(polygon.points)]
    return tuple(tuple(a[i] + t * (b[i] - a[i]) for i in (0, 1)) for t in edge.span)


def edge_side(edge, index):
    return edge.side or SIDES[index]


def segment_endpoints(rect, side, span=(0.0, 1.0)):
    a, b = edge_endpoints(rect, side)
    return tuple((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])) for t in span)


def vector_segment_points(rect, side, width, span, corners):
    if span == (0.0, 1.0):
        return dict(rect_edge_paths(rect, width))[side]
    a, b = segment_endpoints(rect, side, span)
    nominal = dict(rect_edge_paths(rect, width))[side]
    if side in {"top", "bottom"}:
        start_corner, end_corner = (
            ("top_left", "top_right") if side == "top" else ("bottom_right", "bottom_left")
        )
        result = [a, ((a[0] + b[0]) / 2, a[1]), b]
        seam_y = nominal[0][1]
        if start_corner in corners and span[0] == 0:
            result.insert(0, (a[0], seam_y))
        if end_corner in corners and span[1] == 1:
            result.append((b[0], seam_y))
        return tuple(result)
    start = (a[0], nominal[0][1]) if span[0] == 0 else a
    end = (b[0], nominal[-1][1]) if span[1] == 1 else b
    return start, ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2), end


def relative_span(old_rect, old_span, new_rect, side):
    a, b = segment_endpoints(old_rect, side, old_span)
    c, d = segment_endpoints(new_rect, side)
    axis = 0 if side in {"top", "bottom"} else 1
    length = d[axis] - c[axis]
    return (a[axis] - c[axis]) / length, (b[axis] - c[axis]) / length
