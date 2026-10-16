"""``layer.delete`` -> ``DeleteLayerCommand`` per target layer (confirmation required)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...commands_layer_tree import DeleteLayerCommand
from ..action_context import ActionContext
from ..action_descriptor import ActionDescriptor
from ..action_effect import ActionEffect
from ..action_outcome import ActionOutcome
from ..resolved_target import ResolvedTarget

__all__ = ["LayerDeleteAction"]


class LayerDeleteAction:
    descriptor: ClassVar[ActionDescriptor] = ActionDescriptor(
        action_id="layer.delete",
        version=1,
        title="Delete layer",
        category="layer",
        target_kind="layer",
        effects=(ActionEffect("mutation", "delete layer", confirmation_required=True),),
    )

    def execute(
        self, context: ActionContext, target: ResolvedTarget, params: Mapping[str, Any]
    ) -> ActionOutcome:
        done = sum(
            1 for layer_id in target.ids if context.canvas.submit(DeleteLayerCommand(layer_id))
        )
        if not done:
            return ActionOutcome.failure("command_rejected")
        return ActionOutcome.success(f"{done} layer(s)")
