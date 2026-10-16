"""Keep existing corner ownership attached to its edge through geometry edits."""

from loomstroke.core.comic_panels.convex_edges import corner_owners
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError


def retained_corner_owners(state, panel_id, polygon, depths):
    owners = list(
        corner_owners(
            polygon.points,
            [0 if r.kind == "page" else depths[r.node_id] for r in polygon.boundaries],
        )
    )
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    previous = state.panel_polygons.get(panel_id)
    if previous:
        refs = previous.boundaries
    else:
        sides = {}
        for index, edge in enumerate(frame.edges):
            side = edge.side or ("top", "right", "bottom", "left")[index]
            sides[side] = edge.boundary_ref
        refs = tuple(sides[side] for side in ("top", "right", "bottom", "left"))
    names = {"top_left": 0, "top_right": 1, "bottom_right": 2, "bottom_left": 3}
    inherited = {}
    for edge in frame.edges:
        for corner in edge.owned_corners:
            index = int(corner[7:]) if corner.startswith("vertex:") else names[corner]
            if index >= len(refs):
                raise PanelFrameError("frame corner no longer matches its geometry")
            pair = frozenset((refs[index - 1], refs[index]))
            existing = inherited.get(pair)
            if existing is not None and existing != edge.boundary_ref:
                raise PanelFrameError("frame corner has multiple owners")
            inherited[pair] = edge.boundary_ref
    for vertex, boundary in enumerate(polygon.boundaries):
        pair = frozenset((polygon.boundaries[vertex - 1], boundary))
        owner = inherited.get(pair)
        if owner in pair:
            owners[vertex] = polygon.boundaries.index(owner)
    return tuple(owners)
