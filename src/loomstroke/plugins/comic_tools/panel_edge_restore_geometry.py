"""Map the displayed fragment of a retained vector source, without moving its gaps."""

import copy
from dataclasses import replace

from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.plugins.vector_tools.common import Anchor, VectorPath

from .panel_edge_geometry import segment_endpoints, vector_segment_points


def retain_fragment_endpoints(payload, current, fragment):
    """Record the source-space interval exposed by a new display window."""
    result = copy.deepcopy(payload)
    source = payload.get("restoration_fragment_endpoints", payload.get("restoration_endpoints"))
    if source is None:
        return result
    if tuple(map(tuple, source)) == tuple(map(tuple, current)):
        result["restoration_fragment_endpoints"] = [list(p) for p in fragment]
        return result
    a, b = current
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= 0:
        raise PanelFrameError("invalid vector restoration fragment")
    c, d = source
    points = []
    for p in fragment:
        t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length_squared
        points.append([c[0] + t * (d[0] - c[0]), c[1] + t * (d[1] - c[1])])
    result["restoration_fragment_endpoints"] = points
    return result


def restored_rect_paths(obj, payload, old_rect, rect, edge, side):
    from .panel_convex_plan import map_boundary_point, map_boundary_vector  # noqa: PLC0415

    nominal = vector_segment_points(
        old_rect, side, obj.style.stroke_width, edge.span, edge.owned_corners
    )
    if payload.get("restoration_nominal") or (
        len(obj.paths) == 1 and [a.pos for a in obj.paths[0].anchors] == list(nominal)
    ):
        return [
            VectorPath(
                [
                    Anchor(p)
                    for p in vector_segment_points(
                        rect, side, obj.style.stroke_width, edge.span, edge.owned_corners
                    )
                ]
            )
        ]
    old = payload.get("restoration_fragment_endpoints", payload.get("restoration_endpoints"))
    if old is None:
        old = segment_endpoints(old_rect, side, edge.span)
    new = segment_endpoints(rect, side, edge.span)
    if tuple(map(tuple, old)) == tuple(map(tuple, new)):
        return obj.paths
    return [
        replace(
            path,
            anchors=[
                replace(
                    a,
                    pos=map_boundary_point(a.pos, old, new),
                    h_in=map_boundary_vector(a.h_in, old, new),
                    h_out=map_boundary_vector(a.h_out, old, new),
                )
                for a in path.anchors
            ],
        )
        for path in obj.paths
    ]
