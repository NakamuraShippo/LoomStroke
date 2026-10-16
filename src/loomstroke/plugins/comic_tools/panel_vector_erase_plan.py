"""Non-destructive vector frame gaps retain the logical panel and vector source."""

from loomstroke.core.comic_panels.edge_erasure import append_erasure, erased_edge_mask
from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError

from .panel_edge_geometry import registered_endpoints
from .panel_edit_gate import require_panel_editable
from .panel_source_guard import source_guard


def vector_erase_edge_command(state, panel_id, edge_id, points, width, *, page_size):
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    edge = next((e for e in frame.edges if e.edge_id == edge_id), None)
    if edge is None or edge.render_mode != "vector":
        raise PanelFrameError("edge is not a vector part")
    require_panel_editable(state, panel_id, edges=(edge,))
    part = state.panel_parts[edge.part_layer_id]
    marker = part.values["metadata"].get(PART_KEY, {})
    if part.values["layer_mask"] is not None and not any(
        marker.get(key) for key in ("nearest_mask", "span_window", "erase_strokes")
    ):
        raise PanelFrameError("vector erase requires a derived edge mask")
    endpoints = registered_endpoints(state, panel_id, edge, frame.edges.index(edge))
    metadata = append_erasure(part.values["metadata"], endpoints, points, width)
    # Apply only the newly added gesture to the already composed saved mask.
    stroke = metadata[PART_KEY]["erase_strokes"][-1]
    single = {PART_KEY: {"erase_strokes": [stroke]}}
    mask = erased_edge_mask(part.values["layer_mask"], single, endpoints, page_size)
    return PanelEditCommand(
        [
            source_guard(state, page_size=page_size),
            PanelLayerPatchCommand(
                part.layer_id,
                before={
                    "metadata": part.values["metadata"],
                    "layer_mask": part.values["layer_mask"],
                },
                after={"metadata": metadata, "layer_mask": mask},
            ),
        ],
        label="Erase vector frame edge",
    )
