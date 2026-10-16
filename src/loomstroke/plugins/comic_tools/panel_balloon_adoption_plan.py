"""Give existing balloon creation routes one atomic panel-membership transaction."""

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.command_types import Command

from .panel_balloon_plan import attach_balloon_command
from .panel_source_guard import source_guard


class AdoptCreatedBalloonCommand(Command):
    vector_object = None
    _layer_id = "__loomstroke_comic_tools__"

    def __init__(self, controller, balloon_id, panel_id, *, standalone=False):
        super().__init__()
        self.controller, self.balloon_id, self.panel_id = controller, balloon_id, panel_id
        self.command = None
        self.standalone = standalone
        self.last_error = ""

    def apply(self, canvas):
        if self.command is None:
            state = self.controller.read_state(canvas, page_size=(canvas._width, canvas._height))
            try:
                self.command = attach_balloon_command(
                    state, self.balloon_id, self.panel_id, standalone=self.standalone
                )
            except PanelFrameError as exc:
                self.last_error = str(exc)
                return False
        result = self.command.apply(canvas)
        self.last_error = self.command.last_error
        self._executed = result
        return result

    def revert(self, canvas):
        if self.command is None or not self.command.revert(canvas):
            return False
        self._executed = False
        return True


def adopt_created_balloon(controller, state, record, command):
    target = state.panel_records.get(record.panel_id)
    frame = read_frame_layer(target.metadata) if target else None
    return PanelEditCommand(
        [
            source_guard(state),
            command,
            AdoptCreatedBalloonCommand(
                controller, record.balloon_id, record.panel_id, standalone=frame is None
            ),
        ],
        label=command.label,
    )
