"""Owned, non-document text preview using the existing canvas vector preview slot."""

from dataclasses import asdict

from .service import text_props_from_object
from .text_frames import build_balloon_text_object


class DialoguePreview:
    def __init__(self, tool, edit):
        self.tool = tool
        self.edit = edit
        self.canvas = getattr(tool.host_api, "canvas", None)
        self.object = None
        self.closed = False

    def check_page(self):
        if self.tool.host_api.pages.get_active_page_id() != self.edit.page_id:
            self.close()

    def update(self, text):
        self.check_page()
        if self.closed or self.canvas is None:
            return
        current_preview = getattr(self.canvas, "_vector_preview", None)
        if current_preview is not None and current_preview is not self.object:
            self.close()  # Never steal another tool's preview.
            return
        state = self.tool.read_state()
        obj = state.text_objects.get(self.edit.balloon_id) if state else None
        if (
            state is None
            or self.edit.balloon_id not in state.balloons
            or not state.text_layer_id
            or (text_props_from_object(obj).text if obj else "") != self.edit.text
        ):
            self.close()
            return
        props = asdict(text_props_from_object(obj))
        props["text"] = text
        self.object = build_balloon_text_object(
            state.balloons[self.edit.balloon_id],
            **props,
            previous_meta=getattr(obj, "meta", None),
        )
        # Same runtime-only HostAPI.canvas convention as the inline text tool.
        self.canvas._vector_preview = self.object
        self.canvas._vector_preview_layer_id = state.text_layer_id
        self.canvas.notify_vector_preview_changed()

    def close(self):
        self.closed = True
        if self.object is not None and getattr(self.canvas, "_vector_preview", None) is self.object:
            self.canvas._vector_preview = None
            # Notify while the layer id is still available: invalidate only that layer.
            self.canvas.notify_vector_preview_changed()
            self.canvas._vector_preview_layer_id = None
        self.object = None
