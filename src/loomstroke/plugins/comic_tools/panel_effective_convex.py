"""Resolve merged leaf ownership into a convex outer contour with provenance."""

from loomstroke.core.comic_panels.convex_union import convex_hull, outer_boundaries
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.polygon_allocation import polygon_overlap

from .panel_convex_geometry import ConvexPanel, compute_convex_panels


def effective_convex_panels(layout, records, page_size):
    leaves = compute_convex_panels(layout, page_size)
    owners = {}
    for panel_id, record in records.items():
        frame = read_frame_layer(record.metadata)
        ids = frame.layout_leaf_ids if frame else (panel_id,)
        if not set(ids) <= leaves.keys():
            raise PanelFrameError("merged convex panel has missing layout leaves")
        for leaf in ids:
            if leaf in owners:
                raise PanelFrameError("convex layout leaf has multiple owners")
            owners[leaf] = panel_id
    if owners.keys() != leaves.keys():
        raise PanelFrameError("convex layout has unowned leaves")
    result = {}
    for panel_id, record in records.items():
        frame = read_frame_layer(record.metadata)
        ids = frame.layout_leaf_ids if frame else (panel_id,)
        source = [leaves[leaf] for leaf in ids]
        if len(source) == 1:
            result[panel_id] = ConvexPanel(panel_id, source[0].points, source[0].boundaries)
            continue
        points = convex_hull(p for shape in source for p in shape.points)
        refs = outer_boundaries(points, [(shape.points, shape.boundaries) for shape in source])
        for leaf, shape in leaves.items():
            if leaf not in ids and polygon_overlap(points, shape.points) > 1e-6:
                raise PanelFrameError("merged convex region overlaps another panel")
        result[panel_id] = ConvexPanel(panel_id, points, tuple(refs))
    return result
