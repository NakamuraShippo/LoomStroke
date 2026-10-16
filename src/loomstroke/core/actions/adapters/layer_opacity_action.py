"""``layer.set_opacity`` -> ``SetLayerOpacityCommand`` per target layer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...commands_layer_opacity import SetLayerOpacityCommand
from ..action_context import ActionContext
from ..action_descriptor import ActionDescriptor
from ..action_effect import ActionEffect
from ..action_outcome import ActionOutcome
from ..action_parameter import ActionParameter
from ..resolved_target import ResolvedTarget

__all__ = ["LayerOpacityAction"]


class LayerOpacityAction:
    descriptor: ClassVar[ActionDescriptor] = ActionDescriptor(
        action_id="layer.set_opacity",
        version=1,
        title="Set layer opacity",
        category="layer",
        parameters=(ActionParameter("opacity", "float", required=True, minimum=0.0, maximum=1.0),),
        target_kind="layer",
        effects=(ActionEffect("mutation", "layer opacity"),),
    )

    def execute(
        self, context: ActionContext, target: ResolvedTarget, params: Mapping[str, Any]
    ) -> ActionOutcome:
        opacity = float(params["opacity"])
        failed = [
            layer_id
            for layer_id in target.ids
            if not context.canvas.submit(SetLayerOpacityCommand(layer_id, opacity))
        ]
        if failed and len(failed) == len(target.ids):
            return ActionOutcome.failure("command_rejected")
        return ActionOutcome.success(f"{len(target.ids) - len(failed)} layer(s)")
