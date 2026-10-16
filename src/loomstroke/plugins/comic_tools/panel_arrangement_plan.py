"""Exchange existing layout slots while retaining each logical panel identity."""

from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError

from .panel_edit_gate import require_editable_parts, require_panel_editable
from .panel_repair_preview import PanelRepairPreview


def arrangement_preview(state, panel_ids, page_size):
    if len(panel_ids) != 2 or len(set(panel_ids)) != 2:
        raise PanelFrameError("choose two different panels to exchange positions")
    if state.panel_polygons:
        raise PanelFrameError("position exchange currently requires rectangular panel slots")
    owners = {}
    for pid in panel_ids:
        if pid not in state.panel_records:
            raise PanelFrameError("swap target is missing")
        require_panel_editable(state, pid, geometry=True)
        frame = read_frame_layer(state.panel_records[pid].metadata)
        owners[pid] = frame.layout_leaf_ids
    first, second = panel_ids
    owners[first], owners[second] = owners[second], owners[first]
    preview = PanelRepairPreview(state, state.layout, page_size=page_size, ownership=owners)
    targets = set()

    def descendants(item_id):
        targets.add(item_id)
        for child in state.panel_parts[item_id].children:
            descendants(child)

    for item_id, (pid, _unit) in preview.items.items():
        if pid not in panel_ids:
            continue
        x, y, w, h = state.panel_rects[pid]
        nx, ny, nw, nh = preview.rects[pid]
        preview.move(item_id, nx + nw / 2 - x - w / 2, ny + nh / 2 - y - h / 2)
        descendants(item_id)
    require_editable_parts(state, targets)
    for pid in panel_ids:
        if pid in preview.authored_clip_panels:
            x, y, w, h = state.panel_rects[pid]
            nx, ny, nw, nh = preview.rects[pid]
            preview.clip_moves[pid] = (
                round(nx + nw / 2 - x - w / 2),
                round(ny + nh / 2 - y - h / 2),
            )
    preview.geometry_command()
    return preview
