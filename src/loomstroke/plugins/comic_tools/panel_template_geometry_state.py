"""Use explicit template slots while retaining their registered pre-warp geometry."""

from dataclasses import replace

from loomstroke.core.comic_panels.metadata import read_frame_layer, write_frame_layer

from .panel_convex_geometry import compute_convex_panels


def template_geometry_state(state, source, layout, page_size):
    polygons = compute_convex_panels(layout, page_size)
    return replace(
        state,
        panel_records={
            pid: replace(
                record,
                metadata=write_frame_layer(
                    record.metadata,
                    replace(read_frame_layer(record.metadata), layout_leaf_ids=(pid,)),
                ),
            )
            for pid, record in state.panel_records.items()
            if pid in polygons
        },
        panel_sources={pid: value for pid, value in state.panel_sources.items() if pid in polygons},
        panel_rects={
            pid: source.panel_rects.get(pid, polygons[pid].rect)
            for pid in state.panel_records
            if pid in polygons
        },
        panel_polygons={
            pid: polygon for pid, polygon in source.panel_polygons.items() if pid in polygons
        },
    )
