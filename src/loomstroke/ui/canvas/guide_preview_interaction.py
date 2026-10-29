"""Canvas drags update only guide editor fields, never artwork."""

import math

from ...core.composition_snap import nearest_line_point
from ...core.guide_transform import guide_line
from .composition_overlay import composition_groups
from .guide_reference import guide_reference_rect


class GuidePreviewDrag:
    def __init__(self, preview, dialog):
        self.preview, self.dialog = preview, dialog
        self.start = None
        self.offset = (0, 0)
        self.rotating = False
        self.angle = 0
        self.pivot = (0, 0)

    def handle(self, phase, point, rotate=False):
        widget = self.preview.canvas
        host = getattr(widget, "_host_api", None)
        settings = getattr(host, "settings", None)
        if not self.preview.page_matches():
            self.dialog.reject()
            return True
        if settings is not None and settings.get("view/guides_locked", False):
            return True
        if phase == "press":
            values = self.preview.current_values()
            if values is None:
                return True
            if self.preview.attribute == "_layout_dialog_preview":
                options = self.dialog.placement_options()
                lines = tuple(
                    guide_line(
                        axis,
                        value,
                        (widget.state.width, widget.state.height),
                        options.get("rotation", 0),
                        options.get("pivot"),
                    )
                    for axis, value in values[0]
                )
                lines = tuple(line for line in lines if line is not None)
            else:
                lines = tuple(
                    line
                    for group in composition_groups(widget, preview=True).values()
                    for line in group
                )
            threshold = widget.viewport.screen_to_canvas_distance(8)
            hit = nearest_line_point((point.x(), point.y()), lines, threshold)
            self.start = (point.x(), point.y()) if hit is not None else None
            self.offset = self.dialog.placement.offset()
            self.rotating = rotate
            self.angle = self.dialog.placement.angle.value()
            reference = guide_reference_rect(widget, self.dialog.reference.currentData())
            if reference is not None:
                self.pivot = (
                    reference[0] + reference[2] / 2 + self.offset[0],
                    reference[1] + reference[3] / 2 + self.offset[1],
                )
            if rotate and math.dist((point.x(), point.y()), self.pivot) < threshold:
                self.start = None
        elif self.start is not None:
            if self.rotating:
                a = math.atan2(self.start[1] - self.pivot[1], self.start[0] - self.pivot[0])
                b = math.atan2(point.y() - self.pivot[1], point.x() - self.pivot[0])
                delta = math.degrees(math.atan2(math.sin(b - a), math.cos(b - a)))
                self.dialog.placement.angle.setValue((self.angle + delta + 180) % 360 - 180)
            else:
                dx, dy = point.x() - self.start[0], point.y() - self.start[1]
                self.dialog.placement.x.setValue(self.offset[0] + dx)
                self.dialog.placement.y.setValue(self.offset[1] + dy)
            if phase == "release":
                self.start = None
        return True


def route_guide_preview(widget, phase, point, rotate=False):
    for attribute in ("_layout_dialog_preview", "_composition_dialog_preview"):
        preview = getattr(widget, attribute, None)
        if preview is not None and not preview.closed:
            return preview.drag.handle(phase, point, rotate)
    return False
