"""``layer.set_visibility`` -> ``LayerVisibilityCommand`` per target layer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...commands_layer_transform import LayerVisibilityCommand
from ..action_context import ActionContext
from ..action_descriptor import ActionDescriptor
from ..action_effect import ActionEffect
from ..action_outcome import ActionOutcome
from ..action_parameter import ActionParameter
from ..resolved_target import ResolvedTarget

__all__ = ["LayerVisibilityAction"]


class LayerVisibilityAction:
    descriptor: ClassVar[ActionDescriptor] = ActionDescriptor(
        action_id="layer.set_visibility",
        version=1,
        title="Set layer visibility",
        category="layer",
        parameters=(ActionParameter("visible", "bool", required=True),),
        target_kind="layer",
        effects=(ActionEffect("mutation", "layer visibility"),),
    )

    def execute(
        self, context: ActionContext, target: ResolvedTarget, params: Mapping[str, Any]
    ) -> ActionOutcome:
        visible = bool(params["visible"])
        changed = 0
        for layer_id in target.ids:
            if context.canvas.submit(LayerVisibilityCommand(layer_id, visible)):
                changed += 1
        return ActionOutcome.success(f"{changed} layer(s)")
