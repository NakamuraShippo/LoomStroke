"""Coalesced smoothing previews with one committed command per slider drag."""

from PySide6.QtCore import QTimer

from loomstroke.plugins.vector_tools.common import VectorReplaceCommand

from .editing_metadata import clone_text_for_edit


class PathTextSmoothing:
    def __init__(self, controller, key="smoothing"):
        self.controller = controller
        self.key = key
        self.original = self.current = self.pending = self.state = None
        self.layer_id = None
        self.fresh = False
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.flush)

    def begin(self):
        self.controller.nodes.stop()
        for gesture in (self.controller.smoothing, self.controller.handle_smoothing):
            if gesture is not self:
                gesture.cancel()
        self.controller.cancel_drag()
        target = self.controller.target()
        if target is None:
            return
        obj, layer_id, fresh = target
        tool = self.controller.tool
        if not tool._edit_lifecycle.can_edit_layer(layer_id):
            return
        if tool._inline_editor is not None and tool._inline_editor.preedit_text():
            return
        if not fresh:
            tool._finish_inline_edit(commit=True)
            target = self.controller.target()
            if target is None:
                return
            obj, layer_id, _ = target
        self.original = clone_text_for_edit(obj)
        self.layer_id, self.fresh, self.state = layer_id, fresh, tool._inline_state
        if not fresh:
            tool._edit_lifecycle.begin()

    def preview(self, value):
        if self.original is None:
            self.controller.change({self.key: value})
            return
        self.pending = value
        if not self.timer.isActive():
            self.timer.start()

    def flush(self):
        self.timer.stop()
        if self.original is None or self.pending is None:
            return
        if not self.controller.tool._edit_lifecycle.can_edit_layer(self.layer_id):
            self.cancel()
            return
        tool = self.controller.tool
        if not self.fresh:
            hit = tool._selected_text_hit()
            if (
                not tool._edit_lifecycle.same_page()
                or hit is None
                or hit.layer_id != self.layer_id
                or hit.obj != self.original
            ):
                self.cancel()
                return
        value, self.pending = self.pending, None
        self.current = self.controller.patched(self.original, {self.key: value})
        if self.fresh:
            if tool._inline_state is not self.state:
                self.cancel()
                return
            self.state.object_ref = self.current
            tool._update_inline_text_preview()
        else:
            canvas = tool.host_api.canvas
            canvas._vector_preview = self.current
            canvas._vector_preview_layer_id = self.layer_id
            canvas.notify_vector_preview_changed()
        self.controller.refresh_controls()

    def finish(self):
        if self.original is not None and not self.controller.tool._edit_lifecycle.can_edit_layer(
            self.layer_id
        ):
            self.cancel()
            return
        self.flush()
        original, current, fresh, layer_id = self.original, self.current, self.fresh, self.layer_id
        self.original = self.current = self.pending = self.state = None
        if original is None or fresh:
            return
        self.controller.tool._edit_lifecycle.clear()
        self.controller.clear_preview()
        tool = self.controller.tool
        hit = tool._find_text_object_by_id(original.id, layer_id=layer_id)
        if hit is not None and hit.obj == original and current is not None and current != original:
            command = VectorReplaceCommand(original.id, current, layer_id=layer_id)
            if self.key == "handle_smoothness":
                tool.host_api.canvas.submit(command, merge_key=None)
            else:
                tool._submit_command(command)
        self.controller.refresh_controls()

    def cancel(self):
        self.timer.stop()
        if self.original is None:
            return
        original, fresh, state = self.original, self.fresh, self.state
        self.original = self.current = self.pending = self.state = None
        tool = self.controller.tool
        if fresh:
            if state is not None and tool._inline_state is state:
                state.object_ref = original
                tool._update_inline_text_preview()
        else:
            tool._edit_lifecycle.clear()
            self.controller.clear_preview()
        self.controller.refresh_controls()
