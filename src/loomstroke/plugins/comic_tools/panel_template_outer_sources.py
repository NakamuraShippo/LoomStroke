"""Gather processed outer boundaries before allocating a new convex template."""

import copy
import math
from dataclasses import replace

from loomstroke.core.comic_panels.convex_union import convex_hull, outer_boundaries
from loomstroke.core.comic_panels.metadata import read_frame_layer, write_frame_layer

from .panel_convex_geometry import ConvexPanel, compute_convex_panels
from .panel_convex_split_plan import _allocate_sources, _interval
from .panel_edge_geometry import registered_endpoints


def allocate_template_outer_sources(source, projected, layout, command, page_size):
    if not source.panel_polygons:
        return
    points = convex_hull(p for polygon in source.panel_polygons.values() for p in polygon.points)
    boundaries = outer_boundaries(
        points, [(p.points, p.boundaries) for p in source.panel_polygons.values()]
    )
    pool_id = "__template_outer_source_pool__"
    polygon = ConvexPanel(pool_id, points, boundaries)
    edges, raw_edges, outlines = [], [], {}
    first_id = next(iter(source.panel_records))
    first_record = source.panel_records[first_id]
    first_frame = read_frame_layer(first_record.metadata)
    for pid, record in source.panel_records.items():
        frame = read_frame_layer(record.metadata)
        old_polygon = source.panel_polygons[pid]
        for index, edge in enumerate(frame.edges):
            if edge.boundary_ref not in boundaries:
                continue
            interval = _interval(
                registered_endpoints(source, pid, edge, index), polygon, edge.boundary_ref
            )
            if interval is None:
                continue
            _, span, _ = interval
            corners = []
            for corner in edge.owned_corners:
                old_point = old_polygon.points[int(corner[7:])]
                found = next(
                    (i for i, point in enumerate(points) if math.dist(point, old_point) < 1e-7),
                    None,
                )
                if found is not None:
                    corners.append(f"vertex:{found}")
            edges.append(replace(edge, span=span, side="", owned_corners=tuple(corners)))
            raw_edges.append(
                copy.deepcopy(
                    next(
                        raw
                        for raw in record.metadata["frame_layer"]["edges"]
                        if raw["edge_id"] == edge.edge_id
                    )
                )
            )
            outlines[edge.part_layer_id] = old_polygon.points
    metadata = copy.deepcopy(first_record.metadata)
    metadata["frame_layer"]["edges"] = raw_edges
    metadata = write_frame_layer(
        metadata, replace(first_frame, layout_leaf_ids=(pool_id,), edges=tuple(edges))
    )
    parts = source.panel_parts
    pool = replace(
        source,
        panel_records={**source.panel_records, pool_id: replace(first_record, metadata=metadata)},
        panel_sources={**projected.panel_sources, pool_id: source.panel_sources[first_id]},
        panel_polygons={**source.panel_polygons, pool_id: polygon},
        panel_rects={**source.panel_rects, pool_id: polygon.rect},
        panel_parts=parts,
    )
    polygons = compute_convex_panels(layout, page_size)
    _allocate_sources(
        pool,
        pool_id,
        polygons,
        tuple(polygons),
        command,
        layout,
        page_size,
        source_outlines=outlines,
    )
