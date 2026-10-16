"""Immediate repair actions and lightweight choices for the current document."""

from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError

from .panel_repair_preview import PanelRepairPreview


def _prepare_action(state, request, page_size):
    action, args = request
    repair = PanelRepairPreview(
        state, state.layout, page_size=page_size, fixed_frame=action in {"fit", "center"}
    )
    if action == "fit":
        repair.fit(args[0], policy=args[1])
    elif action == "center":
        item_id = args[0]
        panel_id, _ = repair.items[item_id]
        x, y, w, h = repair.content_bounds(item_id)
        px, py, pw, ph = repair.rects[panel_id]
        repair.move(item_id, px + (pw - w) / 2 - x, py + (ph - h) / 2 - y)
    elif action == "edge":
        repair.edge_policy(*args)
    elif action == "clip":
        repair.regenerate_clip(*args)
    else:
        raise PanelFrameError("command was rejected")
    repair.geometry_command()
    if not repair.fixed_frame:
        from .panel_preview_vector_preparation import prepare_preview_vectors  # noqa: PLC0415

        return prepare_preview_vectors(repair)
    return repair


class FrameRepairActions:
    def __init__(self, controls):
        self.controls = controls
        self.workflow = controls.workflow
        self.authored_clips = set()
        self.page_id = None

    def remember(self, repair):
        if repair is not None and not repair.fixed_frame:
            self.authored_clips = set(repair.authored_clip_panels) - repair.regenerated_clips
            self.page_id = self.workflow.page_id()

    def query(self):
        if self.workflow.repair is not None:
            self.remember(self.workflow.repair)
            return self.workflow.repair
        state = getattr(self.controls, "_current_state", None)
        if not state or not state.layout or state.panel_issues or not state.panel_records:
            return None
        # Reading the controls must not build full-page clip masks. The worker
        # validates authored coverage when an explicit operation is requested.
        repair = PanelRepairPreview(
            state, state.layout, page_size=self.workflow.tool._page_size(), inspect_clips=False
        )
        repair.authored_clip_panels = tuple(
            pid
            for pid, record in state.panel_records.items()
            if (frame := read_frame_layer(record.metadata))
            and (
                (pid in self.authored_clips and self.page_id == self.workflow.page_id())
                or (state.panel_parts[frame.content_group_id].values["metadata"] or {})
                .get(PART_KEY, {})
                .get("clip_auto_update")
                is False
            )
        )
        return repair

    def apply(self, action, *args):
        state = self.workflow.state()
        return self.workflow.preparation.start(
            self.controls,
            state,
            (action, args),
            factory=_prepare_action,
            auto_commit=True,
        )
