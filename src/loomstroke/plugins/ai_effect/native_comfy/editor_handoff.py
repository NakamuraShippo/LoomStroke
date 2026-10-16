"""Transfer native snapshots without discarding an existing preset's identity."""

from __future__ import annotations

import copy
from typing import Any

from loomstroke.plugins.ai_effect.editor.export import collect_author_choices
from loomstroke.plugins.ai_effect.gateway_protocol import WorkflowLoadResult


def apply_snapshot(editor: Any, snapshot: dict, *, preserve: bool) -> None:
    output = copy.deepcopy(snapshot["output"])
    native = copy.deepcopy(snapshot["workflow"])
    if not preserve:
        editor.select_gateway("comfyui")
        editor._apply_load_result("ComfyUI.json", WorkflowLoadResult(ok=True, workflow=output))
    else:
        choices = copy.deepcopy(
            collect_author_choices(
                editor._candidates_pane.included(), dict(editor._choices_by_candidate)
            )
        )
        for choice in choices.parameters:
            ref = choice.gateway_ref
            inputs = output.get(str(ref.get("node_id")), {}).get("inputs", {})
            if ref.get("input") in inputs:
                choice.default = copy.deepcopy(inputs[ref["input"]])
        editor._current_workflow = output
        editor.workflow_loaded.emit(output)
        if editor._current_candidates is not None:
            editor._restore_choices_from_import(editor._current_candidates, choices)
            editor._has_outputs = bool(editor._candidates_pane.outputs.included_candidates())
            editor._refresh_export_action_state()
    editor.native_workflow = native
