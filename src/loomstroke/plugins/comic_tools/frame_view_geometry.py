"""Shared, display-only panel positions during a detached boundary repair."""


def panel_view_rects(workflow, state):
    repair = workflow.repair
    return repair.rects if repair is not None else state.panel_rects
