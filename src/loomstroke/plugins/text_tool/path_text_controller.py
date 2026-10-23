"""Explicit path text creation and command-backed interval manipulation."""

import sys
from copy import deepcopy

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QWidget

from loomstroke.core.japanese_text.typography_layout import build_japanese_layout
from loomstroke.core.japanese_text.typography_painter import text_rotation_transform
from loomstroke.core.path_text.geometry import PathTrack
from loomstroke.core.path_text.metadata import DRAFT_PREVIEW_LAYER_ID, path_settings
from loomstroke.core.path_text.sources import canvas_raster_contours, vector_contours
from loomstroke.plugins.vector_tools.common import VectorReplaceCommand

from .common import ensure_text_vector_object, extract_text_payload
from .editing_metadata import clone_text_for_edit, replace_edited_text
from .path_text_controls import PathTextControls, ts
from .path_text_handle_smoothing import fit_text_handles
from .path_text_node_editor import PathTextNodeEditor
from .path_text_overlay import PathTextOverlay
from .path_text_smoothing import PathTextSmoothing

# Retain the legacy key name without reading, writing or deleting its stored value.
OVERFLOW_NOTICE_KEY = "text_tool/path_text_show_overflow_notice"


class PathTextController:
    def __init__(self, tool):
        self.tool = tool
        self.dialog = None
        self.overlay = None
        self.drag = None
        self.contours = []
        self._layout_payload = None
        self._layout_diagnostics = ()
        self.smoothing = PathTextSmoothing(self)
        self.handle_smoothing = PathTextSmoothing(self, "handle_smoothness")
        self.nodes = PathTextNodeEditor(self)
        self._notice_editor = None

    def target(self):
        if self.nodes.active and self.nodes.valid():
            preview = self.tool.host_api.canvas._vector_preview
            if preview is not None and preview.id == self.nodes.canvas.object_id:
                return preview, self.nodes.canvas.layer_id, False
        for gesture in (self.smoothing, self.handle_smoothing):
            if gesture.current is not None:
                return gesture.current, gesture.layer_id, gesture.fresh
        if self.drag is not None and self.drag[-1] is not None:
            return self.drag[-1], self.drag[2], self.drag[3]
        state = self.tool._inline_state
        if (
            state is not None
            and state.object_ref is not None
            and path_settings(extract_text_payload(state.object_ref)) is not None
        ):
            return state.object_ref, state.layer_id, state.object_id is None
        hit = self.tool._selected_text_hit()
        if hit is not None and path_settings(hit.payload) is not None:
            return hit.obj, hit.layer_id, False
        return None

    @staticmethod
    def preview_layer_id(state):
        if (
            state.object_id is None
            and state.object_ref is not None
            and path_settings(extract_text_payload(state.object_ref)) is not None
        ):
            return DRAFT_PREVIEW_LAYER_ID
        return state.layer_id

    def begin(self):
        if not self.tool.host_api:
            return
        if self.target() is None:
            hit = self.tool._selected_vector_hit()
            if hit is not None and hit.payload is not None:
                self.tool._show_status(ts("Select a vector path, a selection, or a raster layer."))
                return
            margin = max(64, self.tool._settings().font_size_pt * 4)
            self.contours = (
                vector_contours(hit.obj, margin=margin)
                if hit is not None
                else canvas_raster_contours(self.tool.host_api.canvas, margin=margin)
            )
            if not self.contours:
                self.tool._show_status(
                    ts("No usable contour. Select an object or make a selection first.")
                )
                return
            self.tool._finish_inline_edit(commit=True)
            rect, path = self.contours[0]
            if not self.tool._start_inline_edit(
                initial_text="", rect=rect, word_wrap=True, layer_id=None, target_hit=None
            ):
                return
            state = self.tool._inline_state
            obj = state.object_ref or ensure_text_vector_object(
                object_id="text-inline-preview",
                content="",
                rect=rect,
                settings=self.tool._settings(),
                word_wrap=True,
            )
            obj = clone_text_for_edit(obj)
            obj.meta["text_tool"]["path_text"] = {
                "version": 1,
                "path": deepcopy(path),
                "start": 0.0,
                "end": 1.0,
                "reverse": False,
                "full_loop": bool(path.get("closed")),
                "offset": 0.0,
                "rotate": True,
                "align": "left",
            }
            obj.meta["text_tool"]["vertical"] = False
            obj.meta["text_tool"].get("style_map", {}).get("default", {}).update(vertical=False)
            state.object_ref = obj
            self.tool._update_inline_text_preview()
        self.attach()
        if self.dialog is None:
            self.dialog = PathTextControls(self, self.tool._canvas_widget())
        self.dialog.contour.blockSignals(True)
        self.dialog.contour.clear()
        for i in range(max(1, len(self.contours))):
            self.dialog.contour.addItem(str(i + 1), i)
        self.dialog.contour.setEnabled(len(self.contours) > 1)
        self.dialog.contour.blockSignals(False)
        self.refresh_controls()
        self.dialog.show()
        self.edit()

    def attach(self):
        canvas = self.tool._canvas_widget()
        if self.target() is None or canvas is None:
            return
        editor = self.tool._inline_editor
        if (
            sys.platform == "win32"
            and getattr(canvas, "native_surface_active", False)
            and editor is not None
            and editor.testAttribute(Qt.WidgetAttribute.WA_NativeWindow)
            and editor.windowHandle() is not None
        ):
            # Qt's transparent child HWND can copy stale backing-store pixels
            # over the swapchain. This editor only receives input: the canvas
            # already paints path glyphs, selection and caret. Set opacity on
            # the child handle itself, never editor.window() (the MainWindow).
            editor.windowHandle().setOpacity(0.0)
        if self.overlay is None:
            self.overlay = PathTextOverlay(self, canvas)
        if not isinstance(canvas, QWidget):
            return
        if self.dialog is None:
            self.dialog = PathTextControls(self, canvas)
        if editor is not self._notice_editor:
            self.detach_editor()
            self._notice_editor = editor
            if editor is not None:
                editor.textChanged.connect(self.refresh_controls)
        self.dialog.body.bind(editor)
        self.refresh_controls()
        self.dialog.show()

    def detach_editor(self):
        if self._notice_editor is not None:
            self._notice_editor.textChanged.disconnect(self.refresh_controls)
            self._notice_editor = None
        if self.dialog is not None:
            self.dialog.body.detach()

    def edit_body(self):
        self.nodes.stop()
        if self.tool._inline_editor is None:
            self.edit()
        if self.dialog is not None and self.tool._inline_editor is not None:
            self.dialog.activateWindow()
            self.dialog.body.setFocus()

    def sync_body_wrap(self, editor):
        if self.dialog is not None and self.dialog.body._editor is editor:
            self.dialog.body.sync_wrap()

    def close(self):
        self.nodes.stop()
        self.smoothing.cancel()
        self.handle_smoothing.cancel()
        self.cancel_drag()
        self.detach_editor()
        if self.overlay is not None:
            self.overlay.close_overlay()
            self.overlay = None
        if self.dialog is not None:
            dialog, self.dialog = self.dialog, None
            dialog.hide()
            dialog.deleteLater()
        self.contours = []

    def edit(self):
        self.nodes.stop()
        if self.tool._inline_state is None:
            hit = self.tool._selected_text_hit()
            if hit is not None and path_settings(hit.payload) is not None:
                self.tool._edit_existing_text(hit)
        editor = self.tool._inline_editor
        if editor is not None:
            editor.window().activateWindow()
            editor.setFocus()

    def refresh_controls(self):
        if self.dialog is None:
            return
        target = self.target()
        self.dialog.setEnabled(target is not None)
        if target is None:
            return
        payload = extract_text_payload(target[0])
        preview = getattr(self.tool.host_api.canvas, "_vector_preview", None)
        if self.tool._inline_editor is not None and getattr(preview, "id", None) == target[0].id:
            payload = extract_text_payload(preview) or payload
        self.dialog.contour.setEnabled(target[2] and len(self.contours) > 1)
        if payload != self._layout_payload:
            self._layout_payload = deepcopy(payload)
            self._layout_diagnostics = build_japanese_layout(payload).diagnostics
        self.dialog.refresh(
            path_settings(payload), "path_text_overflow" in self._layout_diagnostics
        )
        if self.dialog.body.isReadOnly() and self.dialog.body.toPlainText() != payload.get(
            "content", ""
        ):
            self.dialog.body.setPlainText(payload.get("content", ""))

    def choose_contour(self, index):
        target = self.target()
        if target is None or not target[2] or not 0 <= index < len(self.contours):
            return
        rect, path = self.contours[index]
        state = self.tool._inline_state
        state.rect = rect
        state.object_ref = replace_edited_text(
            state.object_ref, self.tool._inline_editor.toPlainText(), rect=rect
        )
        self.change(
            {
                "path": deepcopy(path),
                "handle_source_path": None,
                "handle_smoothness": 0.0,
                "start": 0.0,
                "end": 1.0,
                "full_loop": bool(path.get("closed")),
            }
        )
        self.tool._sync_inline_editor_geometry()

    def change(self, patch):
        self.nodes.stop()
        target = self.target()
        if target is None:
            return
        obj, layer_id, fresh = target
        if not self.tool._edit_lifecycle.can_edit_layer(layer_id):
            return
        if self.tool._inline_editor is not None and self.tool._inline_editor.preedit_text():
            return
        if not fresh:
            self.tool._finish_inline_edit(commit=True)
            target = self.target()
            if target is None:
                return
            obj, layer_id, _ = target
        updated = self.patched(obj, patch)
        if updated == obj:
            return
        if fresh:
            self.tool._inline_state.object_ref = updated
            self.tool._update_inline_text_preview()
        else:
            command = VectorReplaceCommand(obj.id, updated, layer_id=layer_id)
            if "handle_smoothness" in patch:
                self.tool.host_api.canvas.submit(command, merge_key=None)
            else:
                self.tool._submit_command(command)
        self.refresh_controls()

    def swap_endpoints(self):
        self.cancel_drag()
        target = self.target()
        if target is None:
            return
        settings = path_settings(extract_text_payload(target[0]))
        self.change(
            {
                "start": settings.get("end", 1.0),
                "end": settings.get("start", 0.0),
                "full_loop": bool(settings.get("full_loop")),
            }
        )

    @staticmethod
    def patched(obj, patch):
        if patch.keys() == {"handle_smoothness"}:
            return fit_text_handles(obj, patch["handle_smoothness"])
        updated = clone_text_for_edit(obj)
        settings = updated.meta["text_tool"]["path_text"]
        if (
            "reverse" in patch
            and not settings["path"].get("closed")
            and bool(patch["reverse"]) != bool(settings.get("reverse"))
        ):
            settings["start"], settings["end"] = settings.get("end", 1), settings.get("start", 0)
        settings.update({k: v for k, v in patch.items() if k != "handle_smoothness"})
        if settings.get("handle_source_path") is None:
            settings.pop("handle_source_path", None)
        if "handle_smoothness" in patch:
            updated = fit_text_handles(updated, patch["handle_smoothness"])
            settings = updated.meta["text_tool"]["path_text"]
        if ("start" in patch or "end" in patch) and "full_loop" not in patch:
            settings["full_loop"] = False
        return updated

    @staticmethod
    def track_and_rotation(obj):
        payload = extract_text_payload(obj)
        return PathTrack.from_payload(payload), text_rotation_transform(payload)

    @staticmethod
    def handle_points(track, map_point):
        points = {
            "start": map_point(track.at_fraction(track.start)[0]),
            "end": map_point(track.at_fraction(track.end)[0]),
        }
        if (points["start"] - points["end"]).manhattanLength() < 15:
            points["start"] += QPointF(-8, 0)
            points["end"] += QPointF(8, 0)
        return points

    def handle_at(self, x, y):
        if self.overlay is None:
            return None
        target = self.target()
        widget = self.tool._canvas_widget()
        if target is None or widget is None:
            return None
        track, rotation = self.track_and_rotation(target[0])
        if track is None:
            return None

        def to_widget(p):
            p = rotation.map(p)
            return QPointF(*widget.viewport.canvas_to_widget(p.x(), p.y()))

        point = QPointF(*widget.viewport.canvas_to_widget(x, y))
        for name, position in self.handle_points(track, to_widget).items():
            if (point - position).manhattanLength() <= 12:
                return name
        return None

    def pointer(self, phase, point, **modifiers):  # noqa: PLR0911, PLR0912
        if self.nodes.pointer(phase, point, **modifiers):
            return True
        if self.overlay is None and self.drag is None:
            return False
        x, y = float(point.pos[0]), float(point.pos[1])
        if phase == "hover":
            if self.handle_at(x, y) is None:
                return False
            self.tool._canvas_widget().setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            return True
        if phase == "press":
            name = self.handle_at(x, y)
            if name is None:
                return False
            target = self.target()
            if not self.tool._edit_lifecycle.can_edit_layer(target[1]):
                return True
            if self.tool._inline_editor is not None and self.tool._inline_editor.preedit_text():
                return True
            if not target[2]:
                self.tool._finish_inline_edit(commit=True)
                target = self.target()
            if target is None:
                return True
            self.drag = (name, clone_text_for_edit(target[0]), target[1], target[2], None)
            self._drag_press = QPointF(*self.tool._canvas_widget().viewport.canvas_to_widget(x, y))
            self.tool._reset_state()
            capture = getattr(getattr(self.tool.host_api, "input", None), "pointer_capture", None)
            if callable(capture):
                capture(self.tool.descriptor().id)
            return True
        if self.drag is None:
            return False
        name, original, layer_id, fresh, _ = self.drag
        screen_point = QPointF(*self.tool._canvas_widget().viewport.canvas_to_widget(x, y))
        if self.drag[-1] is None and (screen_point - self._drag_press).manhattanLength() < 3:
            if phase == "release":
                self.cancel_drag()
            return True
        track, rotation = self.track_and_rotation(original)
        fraction = track.project(rotation.inverted()[0].map(QPointF(x, y)))
        updated = self.patched(original, {name: fraction})
        self.drag = name, original, layer_id, fresh, updated
        if fresh:
            state = self.tool._inline_state
            if state is not None:
                state.object_ref = updated
                self.tool._update_inline_text_preview()
        else:
            canvas = self.tool.host_api.canvas
            canvas._vector_preview = updated
            canvas._vector_preview_layer_id = layer_id
            canvas.notify_vector_preview_changed()
        if phase == "release":
            self.drag = None
            if not fresh:
                self.clear_preview()
                current = self.tool._find_text_object_by_id(original.id, layer_id=layer_id)
                if current is not None and current.obj == original and updated != original:
                    self.tool._submit_command(
                        VectorReplaceCommand(original.id, updated, layer_id=layer_id)
                    )
            self.release_capture()
        return True

    def clear_preview(self):
        canvas = self.tool.host_api.canvas if self.tool.host_api else None
        if canvas is not None:
            canvas._vector_preview = None
            canvas._vector_preview_layer_id = None
            canvas.notify_vector_preview_changed()

    def release_capture(self):
        release = getattr(getattr(self.tool.host_api, "input", None), "release_capture", None)
        if callable(release):
            release(self.tool.descriptor().id)

    def cancel_drag(self):
        if self.drag is None:
            return False
        _, original, _, fresh, _ = self.drag
        self.drag = None
        if fresh and self.tool._inline_state is not None:
            self.tool._inline_state.object_ref = original
            self.tool._update_inline_text_preview()
        else:
            self.clear_preview()
        self.release_capture()
        return True
