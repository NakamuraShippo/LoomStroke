"""``layer.rename`` -> ``RenameLayerCommand``; ``{name}`` in the new name keeps the old one."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...commands_layer_rename import RenameLayerCommand
from ..action_context import ActionContext
from ..action_descriptor import ActionDescriptor
from ..action_effect import ActionEffect
from ..action_outcome import ActionOutcome
from ..action_parameter import ActionParameter
from ..resolved_target import ResolvedTarget

__all__ = ["LayerRenameAction"]


class LayerRenameAction:
    descriptor: ClassVar[ActionDescriptor] = ActionDescriptor(
        action_id="layer.rename",
        version=1,
        title="Rename layer",
        category="layer",
        parameters=(
            ActionParameter(
                "name", "str", required=True, description="Use {name} for the current name"
            ),
        ),
        target_kind="layer",
        effects=(ActionEffect("mutation", "layer name"),),
    )

    def execute(
        self, context: ActionContext, target: ResolvedTarget, params: Mapping[str, Any]
    ) -> ActionOutcome:
        template = str(params["name"])
        renamed = 0
        for layer_id in target.ids:
            current = context.layer_name(layer_id)
            new_name = template.replace("{name}", current).strip()
            if not new_name:
                continue
            if context.canvas.submit(RenameLayerCommand(layer_id, new_name)):
                renamed += 1
        if not renamed:
            return ActionOutcome.failure("command_rejected")
        return ActionOutcome.success(f"{renamed} layer(s)")
