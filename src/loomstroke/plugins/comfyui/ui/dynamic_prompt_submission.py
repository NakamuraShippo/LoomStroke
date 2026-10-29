"""Apply live choices to a submission clone, leaving the workflow template intact."""

from __future__ import annotations

from .prompt_composer import PromptComposerWidget


def apply_dynamic_prompts(node_tabs, workflow) -> None:
    roots = [node_tabs]
    tabs = getattr(node_tabs, "tab_widget", None)
    for box in getattr(tabs, "detached_docks", {}).values():
        if box.value is not None:
            roots.append(box.value)
    composers = {composer for root in roots for composer in root.findChildren(PromptComposerWidget)}
    for composer in composers:
        if not composer.is_composer_active():
            continue
        node = workflow.nodes.get(composer.property("prompt_node_id"))
        name = composer.property("prompt_param_name")
        if node is None or name not in node.inputs or not isinstance(node.inputs[name], str):
            continue
        node.inputs[name] = composer.resolve_for_generation()
