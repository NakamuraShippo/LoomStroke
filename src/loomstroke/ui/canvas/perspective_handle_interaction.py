"""Session-only vanishing-point drag preview; commit through the existing controller."""

from PySide6.QtCore import Qt

from loomstroke.core.drawing_assist.perspective_edit import (
    nearest_vanishing_point,
    with_vanishing_point,
)


class PerspectiveHandleInteraction:
    def __init__(self, controller):
        self.controller = controller
        self.enabled = False
        self.original = None
        self.preview = None
        self.page = None
        self.tool = None
        self.index = None

    def cancel(self):
        changed = self.preview is not None
        self.original = self.preview = None
        self.index = None
        if changed:
            self.controller._repaint()

    def _valid(self, widget):
        if not self.enabled or not self.controller.overlay_visible:
            return False
        c, assist = self.controller, self.controller.active_assist()
        return (
            self.enabled
            and c.overlay_visible
            and assist is not None
            and assist.type == "perspective"
            and assist.enabled
            and not assist.locked
            and (
                self.original is None
                or (
                    c.page_id() == self.page
                    and assist == self.original
                    and widget._active_tool_id() == self.tool
                )
            )
        )

    def route(self, widget, event, phase):
        if phase == "cancel":
            consumed = self.original is not None
            self.cancel()
            return consumed
        if (
            (phase != "press" and self.original is None)
            or not self._valid(widget)
            or widget._input_handler_intercepts(event)
        ):
            self.cancel()
            return False
        if phase in {"press", "release"} and event.button() != Qt.LeftButton:
            return False
        pos = event.position()
        point = widget.viewport.widget_to_canvas(pos.x(), pos.y())
        if phase == "press":
            return self._press(widget, point)
        preview = with_vanishing_point(self.original, self.index, point)
        issues = self.controller.issues(preview)
        if not issues:
            self.preview = preview
            self.controller._repaint()
        if phase == "release":
            updated, original = self.preview, self.original
            self.cancel()
            if not issues and updated != original:
                self.controller.update(updated)
        return True

    def _press(self, widget, point):
        assist = self.controller.active_assist()
        radius = widget.viewport.screen_to_canvas_distance(10)
        index = nearest_vanishing_point(assist, point, radius)
        if index is None:
            return False
        self.original = self.preview = assist
        self.index, self.page, self.tool = (
            index,
            self.controller.page_id(),
            widget._active_tool_id(),
        )
        return True


def route_assist_handles(widget, event, phase):
    controller = getattr(widget, "_drawing_assist_controller", None)
    interaction = getattr(controller, "handles", None)
    return bool(interaction and interaction.route(widget, event, phase))
