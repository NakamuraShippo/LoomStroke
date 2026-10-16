"""Select a frame edge and hand its geometry to the existing path editor."""

import copy

from loomstroke.core.comic_panels.drawing_state import panel_input_busy
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError

from .panel_edge_geometry import registered_endpoints
from .panel_edit_gate import require_panel_editable


def edit_vector_edge(workflow, panel_id, edge_id):
    if workflow.draft is not None:
        raise PanelFrameError("Confirm or cancel the current preview first.")
    canvas = workflow.tool._canvas()
    if panel_input_busy(canvas):
        raise PanelFrameError("finish the current stroke or text composition first")
    state = workflow.state()
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    edge = next((edge for edge in frame.edges if edge.edge_id == edge_id), None)
    if edge is None or edge.render_mode != "vector":
        raise PanelFrameError("edge is not a vector part")
    require_panel_editable(state, panel_id, edges=(edge,))
    objects = state.panel_parts[edge.part_layer_id].values["vector_objects"]
    start, _ = registered_endpoints(state, panel_id, edge, frame.edges.index(edge))
    anchors = [
        (sum((anchor.pos[i] - start[i]) ** 2 for i in (0, 1)), obj.id, path_id, anchor_id)
        for obj in objects
        for path_id, path in enumerate(obj.paths)
        for anchor_id, anchor in enumerate(path.anchors)
    ]
    if not anchors:
        raise PanelFrameError("this edge has no editable vector anchors")
    _, object_id, path_id, anchor_id = min(anchors)
    host = workflow.tool.host_api
    manager = getattr(host, "_tool_manager", None) or getattr(host, "tool_manager", None)
    activate = getattr(manager, "activate_tool", None)
    if not callable(activate):
        raise PanelFrameError("vector path editor is unavailable")
    has_tool = getattr(manager, "has_tool", None)
    if callable(has_tool) and not has_tool("vector_path_edit_tool"):
        raise PanelFrameError("vector path editor is unavailable")
    selection = {
        "id": object_id,
        "layer_id": edge.part_layer_id,
        "path": path_id,
        "anchor": anchor_id,
        "auto_return_tool": "comic_tool",
        "auto_return_origin_id": object_id,
    }
    old_layer = canvas.current_layer().id
    old_selection = copy.deepcopy(getattr(canvas, "_vector_selection", None))
    canvas.set_active_layer(edge.part_layer_id)
    canvas._vector_selection = selection
    try:
        if activate("vector_path_edit_tool") is False:
            raise PanelFrameError("vector path editor is unavailable")
    except Exception:
        canvas.set_active_layer(old_layer)
        canvas._vector_selection = old_selection
        raise
    canvas.notify_vector_preview_changed()
    return True
