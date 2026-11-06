"""Symmetric rectangular selection preview and one worker-backed selection commit."""

from PySide6.QtGui import QPainterPath

from ..sdk.assist_geometry import mapped_paths, path_copies
from ..sdk.drawing_assist import ToolAssistBridge
from .free_selection_overlay import FreeSelectionOverlay
from .selection_preparation_controller import SelectionPreparationController


class RectSelectionAssist:
    def __init__(self, tool):
        self.tool = tool
        self.bridge = ToolAssistBridge("loomstroke.basic.selection.rect")
        self.overlay = FreeSelectionOverlay()
        self.preparation = SelectionPreparationController(tool)

    def begin(self, point):
        self.bridge.capture(self.tool.host_api)

    @property
    def active(self):
        session = self.bridge.session
        return session is not None and session.has_copies

    def reset(self):
        self.bridge.end()
        self.overlay.clear()
        self.preparation.cancel()

    def path(self, rect, *, copies=True, union=True):
        path = QPainterPath()
        if rect is not None:
            path.addRect(*map(float, rect))
        return path_copies(path, self.bridge.session, union=union) if copies else path

    def preview(self, rect):
        self.overlay.show(self.tool.host_api, self.path(rect, union=False))

    def commit(self, rect, operation):
        tool, settings = self.tool, self.tool.settings
        path = mapped_paths(self.path(rect, copies=False), self.bridge.session)
        size = tool._canvas_size()
        radius = settings.feather_radius if settings.feather_enabled else 0
        tool._finish_pointer()  # Reset capture before starting the asynchronous request.
        self.preparation.prepare(path, size, radius, settings.pixel_perfect, operation)
