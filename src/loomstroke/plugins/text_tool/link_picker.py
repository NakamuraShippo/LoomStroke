"""Pick a page-qualified destination without entering text edit or moving a frame."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor

from loomstroke.core.japanese_text.linked_story import story_objects, story_of
from loomstroke.core.japanese_text.story_commands import LinkTextFramesCommand
from loomstroke.core.vector_text_layout import TextLayoutCalculator as Layout

from .link_overlay import LinkOverlay
from .typography_widget import _ts


class TextLinkPicker:
    def __init__(self, tool):
        self.tool = tool
        self.source = None
        self.overlay = None
        self.feedback = []
        self.ordered = []
        self.generation = 0
        self._tokens = []
        self._file_handler = lambda *_: self.cancel()

    def key(self, hit):
        pages = getattr(self.tool.host_api, "pages", None)
        return (pages.get_active_page_id() if pages else "", hit.layer_id, str(hit.obj.id))

    def lookup(self, key):
        pages = getattr(self.tool.host_api, "pages", None)
        if pages is not None:
            entries = pages.document_stories.chain(key)
            return next(obj for current, obj in entries if current == key)
        obj = story_objects(self.tool.host_api.canvas, [key[2]])[0][1]
        story = story_of(obj)
        if story:
            story_objects(self.tool.host_api.canvas, [frame["id"] for frame in story["frames"]])
        return obj

    def begin(self):
        hit = self.tool._selected_text_hit()
        if hit is None:
            return
        if self.tool._inline_editor is not None and self.tool._inline_editor.preedit_text():
            return
        source = self.key(hit)
        self.tool._finish_inline_edit(commit=True)
        self.cancel()
        self.source = source
        self.tool._reset_state()
        events = getattr(self.tool.host_api, "events", None)
        if events is not None:
            self._tokens = [
                (events, name, events.on(name, self._file_handler))
                for name in ("file.new", "file.open", "file.close")
            ]
        self.show_overlay()
        self.tool._show_status(_ts("Click the next text frame. Esc cancels linking."))

    def show_overlay(self):
        canvas = self.tool._canvas_widget()
        if canvas is not None and self.overlay is None:
            self.overlay = LinkOverlay(self, canvas)

    def cancel(self):
        for events, name, token in self._tokens:
            events.off(name, token)
        self._tokens = []
        self.generation += 1
        self.source = None
        self.feedback = []
        self.ordered = []
        if self.overlay is not None:
            self.overlay.timer.stop()
            self.overlay.hide()
            self.overlay.deleteLater()
            self.overlay = None
        canvas = self.tool._canvas_widget()
        if canvas is not None:
            canvas.unsetCursor()

    def compatible(self, key):
        if self.source is None or key == self.source:
            return False
        try:
            a, b = self.lookup(self.source), self.lookup(key)
            sa, sb = story_of(a), story_of(b)
            if sa and sb and sa["id"] == sb["id"]:
                return False
            return self.vertical(a) == self.vertical(b)
        except ValueError:
            return False

    @staticmethod
    def vertical(obj):
        payload = obj.meta["text_tool"]
        return bool(
            Layout.text_style_defaults(payload).get("vertical", payload.get("vertical", False))
        )

    def visible_frames(self):
        if not self.tool.host_api:
            return []
        result = []
        for layer, obj in self.tool.host_api.canvas.get_vector_objects():
            hit = self.tool._find_text_object_by_id(str(obj.id), layer_id=layer)
            if hit is None:
                continue
            key = self.key(hit)
            if self.ordered:
                allowed = key in self.ordered
            else:
                allowed = key in self.feedback or key == self.source or self.compatible(key)
            if allowed:
                result.append((key, hit))
        if self.ordered:
            result.sort(key=lambda pair: self.ordered.index(pair[0]))
        return result

    def pointer(self, point, *, click=False):
        if self.source is None:
            return False
        hit = self.tool._find_text_object_at(float(point.pos[0]), float(point.pos[1]))
        key = self.key(hit) if hit else None
        allowed = key is not None and self.compatible(key)
        canvas = self.tool._canvas_widget()
        if canvas is not None:
            canvas.setCursor(
                QCursor(
                    Qt.CursorShape.PointingHandCursor if allowed else Qt.CursorShape.ForbiddenCursor
                )
            )
        if click and allowed:
            source = self.source
            pages = getattr(self.tool.host_api, "pages", None)
            if pages is not None:
                success = pages.document_stories.link(source, key)
            else:
                success = self.tool.host_api.canvas.submit(
                    LinkTextFramesCommand(source[2], key[2], layer_id=source[1])
                )
            if success:
                self.show_feedback([source, key])
                self.tool._show_status(_ts("Text frames linked."))
                self.tool._properties.sync(force=True)
            else:
                self.tool._show_status(_ts("The text frames could not be linked."))
        return True

    def show_feedback(self, keys):
        self.cancel()
        self.feedback = list(keys)
        self.show_overlay()
        generation = self.generation
        QTimer.singleShot(1800, lambda: self.cancel() if generation == self.generation else None)


def picker(tool):
    if not hasattr(tool, "_link_picker"):
        tool._link_picker = TextLinkPicker(tool)
    return tool._link_picker
