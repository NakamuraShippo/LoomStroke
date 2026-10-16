"""Continue a displayed boundary draft while retaining its original command basis."""

from dataclasses import replace
from functools import partial

from .panel_source_guard import source_guard


def panel_interaction_state(workflow, state):
    if workflow.preparation.task is not None:
        return None
    if workflow.draft is None:
        return state
    repair = workflow.repair
    if (
        repair is None
        or workflow.split is not None
        or workflow.template is not None
        or repair.ownership is not None
        or workflow.draft.page_id != workflow.page_id()
        or not source_guard(repair.state, page_size=repair.page_size).apply(workflow.tool._canvas())
    ):
        return None
    # This state is for hit testing only. Commands keep the original snapshots.
    return replace(
        state, layout=repair.layout, panel_rects=repair.rects, panel_polygons=repair.polygons
    )


def start_boundary_preview(controls, workflow, state, layout, *, auto_commit=False):
    repair = workflow.repair
    factory = None
    if repair is not None:
        state = repair.state
        choices = {
            name: getattr(repair, name).copy()
            for name in (
                "image_repairs",
                "moves",
                "clip_moves",
                "edge_policies",
                "regenerated_clips",
            )
        }
        factory = partial(_prepare_continuation, choices=choices, paint_after=repair.paint_after)
    return workflow.preparation.start(
        controls, state, layout, factory=factory, auto_commit=auto_commit
    )


def _prepare_continuation(state, layout, page_size, *, choices, paint_after):
    from .panel_preview_vector_preparation import prepare_preview_vectors  # noqa: PLC0415
    from .panel_repair_preview import PanelRepairPreview  # noqa: PLC0415

    repair = PanelRepairPreview(state, layout, page_size=page_size)
    for name, values in choices.items():
        setattr(repair, name, values.copy())
    repair.paint_after = paint_after
    repair.geometry_command()
    return prepare_preview_vectors(repair)
