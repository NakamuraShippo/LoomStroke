"""Aggregate leaf geometry for crop/flip/rotate of a merged logical panel."""

from .convex_union import convex_hull, outer_boundaries
from .models import PanelFrameError
from .page_geometry_registry import PanelPageGeometry
from .polygon_allocation import polygon_overlap


def owner_page_geometry(polygons, leaf_ids):
    if not leaf_ids or not set(leaf_ids) <= polygons.keys():
        raise PanelFrameError("merged convex panel has missing layout leaves")
    if len(leaf_ids) == 1:
        return polygons[leaf_ids[0]]
    source = [polygons[leaf] for leaf in leaf_ids]
    before = convex_hull(point for shape in source for point in shape.before_points)
    after = convex_hull(point for shape in source for point in shape.after_points)
    boundaries = outer_boundaries(
        after, [(shape.after_points, shape.after_boundaries) for shape in source]
    )
    for leaf, shape in polygons.items():
        if leaf not in leaf_ids and (
            polygon_overlap(before, shape.before_points) > 1e-6
            or polygon_overlap(after, shape.after_points) > 1e-6
        ):
            raise PanelFrameError("merged convex region overlaps another panel")
    return PanelPageGeometry(before, after, boundaries)
