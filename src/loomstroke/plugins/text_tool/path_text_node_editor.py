"""Path-text node mode delegates anchor operations to the vector edit engine."""

from types import SimpleNamespace

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen

from loomstroke.plugins.vector_tools.edit_tool import VectorPathEditTool

from .path_text_node_canvas import PathTextNodeCanvas
from .path_text_node_geometry import node_object


class _NodeTool(VectorPathEditTool):
    # This mode edits nodes only; whole-text transforms stay with the text tool.
    def _maybe_begin_transform(self, *args, **kwargs):
        return False

    def _hit_test_transform_handle(self, *args, **kwargs):
        return None

    def _sync_style_settings_to_tool_properties(self):
        pass

    def _ensure_handles(self, path, anchor_idx):
        # Selecting a text contour must not replace its existing tangent.
        # Explicit S/M commands create handles when the user requests them.
        return path

    def _flush_pending_commands(self):
        self._pending_commands.clear()


class PathTextNodeEditor:
    def __init__(self, controller):
        self.controller = controller
        self.editor = self.canvas = None
        self._baseline = None
        self._press = None
        self._moved = False

    @property
    def active(self):
        return self.editor is not None

    def start(self, checked=True):
        if not checked:
            self.stop()
            return
        if self.active:
            return
        tool = self.controller.tool
        if tool._inline_editor is not None and tool._inline_editor.preedit_text():
            return
        self.controller.smoothing.cancel()
        self.controller.handle_smoothing.cancel()
        self.controller.cancel_drag()
        target = self.controller.target()
        if target is None or not tool._edit_lifecycle.can_edit_layer(target[1]):
            return
        if target[2] and not target[0].meta["text_tool"].get("content"):
            return
        tool._finish_inline_edit(commit=True)
        hit = tool._selected_text_hit()
        if hit is None or self.controller.target() is None:
            return
        self.canvas = PathTextNodeCanvas(self, hit)
        tool._edit_lifecycle.begin()
        host = tool.host_api
        self.editor = _NodeTool()
        self.editor.activate(
            SimpleNamespace(canvas=self.canvas, settings=host.settings, ui=host.ui)
        )
        self.editor.enable()
        widget = tool._canvas_widget()
        if widget is not None:
            widget.window().activateWindow()
            widget.setFocus()
        self.controller.refresh_controls()

    def stop(self):
        if not self.active:
            return
        if self.editor is not None:
            # A rejected edit must never be retried after a lock/page change.
            self.editor._pending_commands.clear()
            self.editor.disable()
        self.editor = self.canvas = self._baseline = None
        self._press = None
        self.controller.release_capture()
        self.controller.tool._edit_lifecycle.clear()

    def valid(self):
        if not self.active:
            return False
        tool = self.controller.tool
        hit = tool._selected_text_hit()
        return (
            hit is not None
            and hit.obj.id == self.canvas.object_id
            and hit.layer_id == self.canvas.layer_id
            and self.canvas.hit() is not None
            and tool._edit_lifecycle.can_edit_layer(hit.layer_id)
        )

    def pointer(self, phase, point, **modifiers):
        if not self.active:
            return False
        if not self.valid():
            self.stop()
            return True
        if phase == "press":
            # Refresh after Undo/Redo or interval/style changes, without losing
            # node selection between presses handled by the vector engine.
            self.editor._editing_object = self.editor._selected_object_cache = None
            self.editor._hydrate_selection_from_canvas()
            self.editor.on_pointer_down(point, **modifiers)
            self._baseline = self.canvas.hit().obj
            self._press = point.pos
            self._moved = False
            capture = getattr(
                getattr(self.controller.tool.host_api, "input", None), "pointer_capture", None
            )
            if callable(capture):
                capture(self.controller.tool.descriptor().id)
        elif phase == "move":
            if self._changed_during_drag():
                self._cancel_gesture()
                return True
            self._moved |= self._press is not None and point.pos != self._press
            self.editor.on_pointer_move([point])
        elif phase == "release":
            self._moved |= self._press is not None and point.pos != self._press
            if not self._moved or (
                self._baseline is not None
                and self.editor._dragging
                and self.canvas.hit().obj != self._baseline
            ):
                self._cancel_gesture()
            else:
                self.editor.on_pointer_up(point)
            self.editor._pending_commands.clear()
            self._baseline = None
            self._press = None
            self.controller.release_capture()
        else:
            self.editor.on_pointer_hover(point)
        return True

    def _changed_during_drag(self):
        return self._baseline is not None and self.canvas.hit().obj != self._baseline

    def _cancel_gesture(self):
        self.canvas._vector_preview = None
        self.canvas.notify_vector_preview_changed()
        self.editor._dragging = False
        self.editor._anchor_box_start = None
        self.editor._editing_object = self.editor._selected_object_cache = None
        self._baseline = self._press = None
        self.controller.release_capture()

    def key(self, key, pressed):
        if not self.active:
            return False
        if not self.valid():
            self.stop()
            return True
        if pressed and key.lower() in {"esc", "escape"}:
            self.stop()
            return True
        if pressed and not self.editor._dragging:
            self.editor._editing_object = self.editor._selected_object_cache = None
            self.editor._hydrate_selection_from_canvas()
        if key.lower() in {"delete", "backspace"}:
            if self.editor._dragging:
                return True
            selection = self.editor._selected
            if pressed and selection is not None:
                self.editor._delete_anchor_on_selected(selection.path_idx, selection.anchor_idx)
                self.editor._pending_commands.clear()
            return True
        result = self.editor.on_key(key, pressed)
        self.editor._pending_commands.clear()
        return result

    def draw(self, painter, map_point):
        if not self.valid():
            if self.active:
                self.stop()
            return
        if self._changed_during_drag():
            self._cancel_gesture()
        obj = self.canvas._vector_preview or node_object(self.canvas.hit().obj)
        anchors = obj.paths[0].anchors
        path = QPainterPath(map_point(QPointF(*anchors[0].pos)))
        count = len(anchors) if obj.paths[0].closed else len(anchors) - 1
        for i in range(count):
            a, b = anchors[i], anchors[(i + 1) % len(anchors)]
            path.cubicTo(
                map_point(QPointF(*a.pos) + QPointF(*(a.h_out or (0, 0)))),
                map_point(QPointF(*b.pos) + QPointF(*(b.h_in or (0, 0)))),
                map_point(QPointF(*b.pos)),
            )
        painter.setPen(QPen(QColor("#42baff"), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        selection = self.editor._selected
        selected = selection.anchor_idx if selection is not None else -1
        for i, anchor in enumerate(anchors):
            point = map_point(QPointF(*anchor.pos))
            if i == selected:
                for delta in (anchor.h_in, anchor.h_out):
                    if delta is not None:
                        end = map_point(QPointF(*anchor.pos) + QPointF(*delta))
                        painter.drawLine(point, end)
                        painter.setBrush(QColor("white"))
                        painter.drawEllipse(end, 3, 3)
            painter.setBrush(QColor("#42baff" if i == selected else "white"))
            painter.drawRect(QRectF(point.x() - 4, point.y() - 4, 8, 8))
