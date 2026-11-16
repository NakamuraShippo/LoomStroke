"""Follow the active ruler while editing a single path text body."""

from loomstroke.core.path_text.assist_layout import ASSIST_KEY
from loomstroke.core.path_text.metadata import path_settings

from ..sdk.drawing_assist import get_drawing_assist_controller
from .common import extract_text_payload
from .editing_metadata import clone_text_for_edit


class PathTextAssist:
    def __init__(self, tool):
        self.tool = tool
        self.had_active = False

    def sync(self, state):
        obj = state.object_ref
        settings = path_settings(extract_text_payload(obj)) if obj is not None else None
        if settings is None:
            return False
        controller = get_drawing_assist_controller(self.tool.host_api)
        active = controller.active_assist() if controller is not None else None
        if active is not None and controller.issues(active):
            active = None
        if active is None and not self.had_active:
            return False
        current = active.to_dict() if active is not None else None
        self.had_active = active is not None
        if settings.get(ASSIST_KEY) == current:
            return False
        state.object_ref = clone_text_for_edit(obj)
        detached = state.object_ref.meta["text_tool"]["path_text"]
        if current is None:
            detached.pop(ASSIST_KEY, None)
        else:
            detached[ASSIST_KEY] = current
        return True
