"""One captured gesture stages a non-destructive gap on the selected frame edge."""

import math

from loomstroke.core.comic_panels.drawing_state import panel_input_busy
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.fill.fill_gesture_mask import FillGestureMask
from loomstroke.plugins.basic_selection.free_selection_overlay import FreeSelectionOverlay

from .panel_edit_gate import require_panel_editable
from .panel_vector_erase_plan import vector_erase_edge_command


class FrameVectorEraseGesture:
    def __init__(self, workflow):
        self.workflow = workflow
        self.target = None
        self.armed_page = None
        self.points = []
        self.state = None
        self.page_id = None
        self.width = 12.0
        self.overlay = FreeSelectionOverlay()

    def arm(self, panel_id, edge_id, width):
        workflow = self.workflow
        if workflow.draft is not None:
            raise PanelFrameError("Confirm or cancel the current preview first.")
        if panel_input_busy(workflow.tool._canvas()):
            raise PanelFrameError("finish the current stroke or text composition first")
        state = workflow.state()
        frame = read_frame_layer(state.panel_records[panel_id].metadata)
        edge = next((e for e in frame.edges if e.edge_id == edge_id), None)
        if edge is None or edge.render_mode != "vector":
            raise PanelFrameError("edge is not a vector part")
        require_panel_editable(state, panel_id, edges=(edge,))
        if not math.isfinite(width) or width <= 0:
            raise PanelFrameError("invalid vector erase gesture")
        self.cancel()
        self.target, self.width = (panel_id, edge_id), width
        self.armed_page = workflow.page_id()
        workflow.tool._canvas().set_active_layer(edge.part_layer_id)
        return True

    def down(self, point):
        if self.target is None:
            return False
        if self.workflow.page_id() != self.armed_page:
            self.cancel()
            raise PanelFrameError("repair draft belongs to another page")
        if panel_input_busy(self.workflow.tool._canvas()):
            raise PanelFrameError("finish the current stroke or text composition first")
        self.state, self.page_id = self.workflow.state(), self.workflow.page_id()
        self.points = [tuple(map(float, point.pos))]
        self.workflow.tool.host_api.input.pointer_capture("comic_tool")
        return True

    def move(self, points):
        if not self.points:
            return False
        for point in points:
            pos = tuple(map(float, point.pos))
            if all(math.isfinite(v) for v in pos) and math.dist(pos, self.points[-1]) >= 0.5:
                if len(self.points) >= 8192:
                    self.points = self.points[::2]
                self.points.append(pos)
        self.overlay.show(
            self.workflow.tool.host_api, FillGestureMask.path("Gap Fill", self.points, self.width)
        )
        return True

    def up(self, point):
        if not self.points:
            return False
        self.move([point])
        state, target, points, page = self.state, self.target, list(self.points), self.page_id
        self.cancel()
        if self.workflow.page_id() != page:
            raise PanelFrameError("repair draft belongs to another page")
        command = vector_erase_edge_command(
            state, *target, points, self.width, page_size=self.workflow.tool._page_size()
        )
        if not self.workflow.tool._submit(command):
            raise PanelFrameError(command.last_error)
        return True

    def cancel(self):
        restore_overlay = bool(self.points)
        if self.points:
            self.workflow.tool.host_api.input.release_capture("comic_tool")
        self.points, self.target, self.state, self.page_id = [], None, None, None
        self.armed_page = None
        self.overlay.clear()
        install = getattr(self.workflow.tool, "_install_overlay", None)
        if restore_overlay and callable(install):
            install(True)
