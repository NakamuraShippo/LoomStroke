"""``layer.set_blend_mode`` -> ``SetLayerBlendModeCommand`` per target layer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...command_types import BlendMode
from ...commands_layer_blend_mode import SetLayerBlendModeCommand
from ..action_context import ActionContext
from ..action_descriptor import ActionDescriptor
from ..action_effect import ActionEffect
from ..action_outcome import ActionOutcome
from ..action_parameter import ActionParameter
from ..resolved_target import ResolvedTarget

__all__ = ["LayerBlendModeAction"]


class LayerBlendModeAction:
    descriptor: ClassVar[ActionDescriptor] = ActionDescriptor(
        action_id="layer.set_blend_mode",
        version=1,
        title="Set layer blend mode",
        category="layer",
        parameters=(
            ActionParameter(
                "mode",
                "enum",
                required=True,
                choices=tuple(mode.value for mode in BlendMode),
            ),
        ),
        target_kind="layer",
        effects=(ActionEffect("mutation", "layer blend mode"),),
    )

    def execute(
        self, context: ActionContext, target: ResolvedTarget, params: Mapping[str, Any]
    ) -> ActionOutcome:
        mode = str(params["mode"])
        failed = [
            layer_id
            for layer_id in target.ids
            if not context.canvas.submit(SetLayerBlendModeCommand(layer_id, mode))
        ]
        if failed and len(failed) == len(target.ids):
            return ActionOutcome.failure("command_rejected")
        return ActionOutcome.success(f"{len(target.ids) - len(failed)} layer(s)")
