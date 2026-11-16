"""Assist capture for new text frames; existing text editing remains independent."""

from PySide6.QtGui import QPainterPath

from loomstroke.core.drawing_assist.object_direction import object_direction
from loomstroke.core.path_text.metadata import path_settings

from ..basic_selection.free_selection_overlay import FreeSelectionOverlay
from ..sdk.assist_geometry import snap_assist_point
from ..sdk.assist_vector_objects import object_copies
from ..sdk.assisted_vector_command import assisted_create_for_host, assisted_vector_command
from ..sdk.drawing_assist import ToolAssistBridge
from ..vector_tools.common import VectorCreateCommand
from .common import extract_text_payload


class TextDrawingAssist:
    def __init__(self, owner):
        self.owner = owner
        self.bridge = ToolAssistBridge(owner._tool_id)
        self.snapshot = None
        self.captured = False
        self.dragging = False
        self.overlay = FreeSelectionOverlay()

    def capture(self):
        self.bridge.capture(self.owner.host_api)
        self.snapshot = self.bridge.session
        self.captured = True

    def begin(self, point):
        self.capture()
        self.dragging = True
        return self.snap(point)

    def snap(self, point):
        if self.dragging:
            self.bridge.point(point)  # Capture direction without collapsing the text frame.
        return snap_assist_point(self.owner.host_api, point) if self.dragging else point

    def rotation(self):
        return object_direction(self.snapshot, vertical=self.owner._settings().vertical)

    def release(self, point):
        point = self.snap(point)
        self.dragging = False
        self.bridge.end()
        return point

    def cancel(self):
        self.bridge.end()
        self.snapshot = None
        self.captured = False
        self.dragging = False
        self.overlay.clear()

    def command(self, command):
        if not isinstance(command, VectorCreateCommand):
            return command
        if path_settings(extract_text_payload(command.vector_object)) is not None:
            return command
        if not self.captured:
            return assisted_create_for_host(command, self.owner.host_api)
        return assisted_vector_command(command, self.snapshot)

    def select_created(self, command, layer_id):
        obj = command.vector_object
        payload = extract_text_payload(obj)
        frame = payload.get("frame", payload)
        rect = tuple(
            float(frame.get(k, payload.get(k, default)))
            for k, default in (("x", 0), ("y", 0), ("width", 1), ("height", 1))
        )
        angle = float(
            payload.get("transform", {}).get("rotation_deg", payload.get("rotation_deg", 0))
        )
        self.owner._set_inline_selection_for_object(
            obj.id, layer_id, rect, rotation_deg=angle, preview=False
        )

    def preview(self, obj):
        state = self.owner._inline_state
        if (
            state is None
            or state.object_id is not None
            or self.snapshot is None
            or not self.snapshot.has_copies
            or path_settings(extract_text_payload(obj)) is not None
        ):
            self.overlay.clear()
            return
        path = QPainterPath()
        for item in object_copies(obj, self.snapshot):
            if item is obj:
                continue
            for contour in item.paths:
                if not contour.anchors:
                    continue
                path.moveTo(*contour.anchors[0].pos)
                for anchor in contour.anchors[1:]:
                    path.lineTo(*anchor.pos)
                if contour.closed:
                    path.closeSubpath()
        self.overlay.show(self.owner.host_api, path)
