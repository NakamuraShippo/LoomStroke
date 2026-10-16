"""P4 half-plane geometry; one tree remains the authority for inclined cuts."""

import math
from dataclasses import dataclass, replace
from itertools import pairwise

from loomstroke.core.comic_panels.models import BoundaryRef, PanelFrameError

from .layout_geometry_space import geometry_space


@dataclass(frozen=True)
class ConvexPanel:
    panel_id: str
    points: tuple[tuple[float, float], ...]
    boundaries: tuple[BoundaryRef, ...]

    @property
    def rect(self):
        xs, ys = zip(*self.points, strict=True)
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def clip_halfplane(points, normal, offset):
    """Keep dot(normal, p) <= offset, retaining clockwise canvas winding."""
    result = []
    for start, end in zip(points[-1:] + points[:-1], points, strict=True):
        a = normal[0] * start[0] + normal[1] * start[1] - offset
        b = normal[0] * end[0] + normal[1] * end[1] - offset
        if (a <= 1e-9) != (b <= 1e-9):
            ratio = a / (a - b)
            result.append(tuple(start[i] + ratio * (end[i] - start[i]) for i in (0, 1)))
        if b <= 1e-9:
            result.append(end)
    clean = []
    for point in result:
        if not clean or math.dist(clean[-1], point) > 1e-8:
            clean.append(point)
    if len(clean) > 1 and math.dist(clean[0], clean[-1]) <= 1e-8:
        clean.pop()
    return tuple(clean)


def has_inclined_cuts(node):
    return any(node.cut_skews) or any(has_inclined_cuts(child) for child in node.children)


def compute_convex_panels(  # noqa: PLR0915 - recursive half-plane geometry with provenance
    layout, page_size
):
    margin, gutter = layout.margin_px, layout.gutter_px
    (width, height), origin = geometry_space(layout.metadata, page_size)
    if not all(math.isfinite(v) for v in (*page_size, margin, gutter)) or (
        gutter < 0 or margin < 0 or min(width, height) <= 2 * margin
    ):
        raise PanelFrameError("invalid convex page geometry")
    corners = (
        (margin, margin),
        (width - margin, margin),
        (width - margin, height - margin),
        (margin, height - margin),
    )
    planes = (
        (0, -1, -margin, BoundaryRef("page", "top")),
        (1, 0, width - margin, BoundaryRef("page", "right")),
        (0, 1, height - margin, BoundaryRef("page", "bottom")),
        (-1, 0, -margin, BoundaryRef("page", "left")),
    )
    result = {}

    def walk(node, points, inherited):  # noqa: PLR0912 - cut and leaf geometry validation
        if len(points) < 3:
            raise PanelFrameError("inclined cut creates an empty panel")
        if node.is_leaf:
            area = (
                abs(
                    sum(
                        a[0] * b[1] - a[1] * b[0]
                        for a, b in zip(points, points[1:] + points[:1], strict=True)
                    )
                )
                / 2
            )
            if area < 1:
                raise PanelFrameError("inclined cut creates a tiny panel")
            refs = []
            for a, b in zip(points, points[1:] + points[:1], strict=True):
                candidates = [
                    ref
                    for nx, ny, offset, ref in inherited
                    if max(abs(nx * p[0] + ny * p[1] - offset) for p in (a, b)) < 1e-7
                ]
                if not candidates:
                    raise PanelFrameError("convex edge has no boundary provenance")
                refs.append(candidates[0])
            result[node.panel_id] = ConvexPanel(node.panel_id, points, tuple(refs))
            return
        axis = 0 if node.orientation == "vertical" else 1
        other = 1 - axis
        low, high = min(p[axis] for p in points), max(p[axis] for p in points)
        start, end = min(p[other] for p in points), max(p[other] for p in points)
        skews = node.cut_skews or (0,) * (len(node.children) - 1)
        if len(skews) != len(node.children) - 1 or not all(math.isfinite(v) for v in skews):
            raise PanelFrameError("invalid inclined cut values")
        usable = high - low - gutter * (len(node.children) - 1)
        if usable <= 0 or not node.weights or min(node.weights) <= 0:
            raise PanelFrameError("gutter consumes the convex panel")
        cuts, cursor = [], low
        for weight, skew in zip(node.weights[:-1], skews, strict=True):
            cursor += usable * weight / sum(node.weights)
            center = cursor + gutter / 2
            slope = skew * (high - low) / (end - start)
            normal = (1, -slope) if axis == 0 else (-slope, 1)
            offset = center - slope * (start + end) / 2
            cuts.append((normal, offset, gutter * math.hypot(*normal) / 2))
            cursor += gutter
        # Adjacent cuts must retain their order everywhere inside the parent,
        # including both extremes; crossed cuts never mutate the source tree.
        for (na, oa, _), (nb, ob, _) in pairwise(cuts):
            if any(oa - na[other] * p[other] >= ob - nb[other] * p[other] - 1e-8 for p in points):
                raise PanelFrameError("inclined cuts intersect inside their parent")
        for index, child in enumerate(node.children):
            polygon, child_planes = points, list(inherited)
            if index:
                normal, offset, half = cuts[index - 1]
                normal, offset = tuple(-v for v in normal), -(offset + half)
                polygon = clip_halfplane(polygon, normal, offset)
                child_planes.append(
                    (*normal, offset, BoundaryRef("cut", "after", node.node_id, index - 1))
                )
            if index < len(cuts):
                normal, offset, half = cuts[index]
                offset -= half
                polygon = clip_halfplane(polygon, normal, offset)
                child_planes.append(
                    (*normal, offset, BoundaryRef("cut", "before", node.node_id, index))
                )
            walk(child, polygon, child_planes)

    walk(layout.root, corners, planes)
    return {
        panel_id: replace(
            polygon, points=tuple((x + origin[0], y + origin[1]) for x, y in polygon.points)
        )
        for panel_id, polygon in result.items()
    }


def with_cut_skews(layout, node_id, skews, *, page_size):
    found = False

    def rebuild(node):
        nonlocal found
        if node.node_id == node_id:
            if node.is_leaf:
                raise PanelFrameError("a leaf has no cuts to incline")
            found = True
            return replace(node, cut_skews=tuple(skews))
        return replace(node, children=tuple(rebuild(child) for child in node.children))

    changed = replace(layout, root=rebuild(layout.root))
    if not found:
        raise PanelFrameError("inclined cut node is missing")
    compute_convex_panels(changed, page_size)
    return changed
