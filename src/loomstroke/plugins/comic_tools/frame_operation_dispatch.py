"""Route existing comic controls into frame previews without duplicating tools."""

from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError

from .frame_boundary_continuation import start_boundary_preview


def run_frame_action(tool, action):
    """Keep existing command rejection visible without leaking from Qt slots."""
    controls = getattr(tool._panel_widget, "frame_controls", None)
    if controls is not None:
        return controls.run(action)
    try:
        return action()
    except PanelFrameError:
        return False


def frame_controls(tool, state, panel_id=None):
    controls = getattr(tool._panel_widget, "frame_controls", None)
    if controls is None:
        return None
    records = [state.panel_records.get(panel_id)] if panel_id else state.panel_records.values()
    return (
        controls
        if any(record and read_frame_layer(record.metadata) for record in records)
        else None
    )


def preview_layout(tool, state, layout):
    controls = frame_controls(tool, state)
    if controls is None or (
        tool.frame_workflow.repair is None
        and layout.root == state.layout.root
        and (
            layout.gutter_px == state.layout.gutter_px
            and layout.margin_px == state.layout.margin_px
        )
    ):
        return False
    controls.run(
        lambda: start_boundary_preview(
            controls, tool.frame_workflow, state, layout, auto_commit=True
        )
    )
    return True


def preview_template(tool, state, layout):
    controls = frame_controls(tool, state)
    if controls is None:
        return False
    controls.run(lambda: controls.preview_template(layout))
    return True
