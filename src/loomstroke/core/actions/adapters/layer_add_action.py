"""``layer.add`` -> ``AddLayerCommand`` (no target; the page is implied)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from ...commands_layer_tree import AddLayerCommand
from ..action_context import ActionContext
from ..action_descriptor import ActionDescriptor
from ..action_effect import ActionEffect
from ..action_outcome import ActionOutcome
from ..action_parameter import ActionParameter
from ..resolved_target import ResolvedTarget

__all__ = ["LayerAddAction"]


class LayerAddAction:
    descriptor: ClassVar[ActionDescriptor] = ActionDescriptor(
        action_id="layer.add",
        version=1,
        title="Add layer",
        category="layer",
        parameters=(
            ActionParameter("name", "str", required=True),
            ActionParameter("layer_type", "enum", default="raster", choices=("raster", "vector")),
        ),
        target_kind="none",
        effects=(ActionEffect("mutation", "new layer"),),
    )

    def execute(
        self, context: ActionContext, target: ResolvedTarget, params: Mapping[str, Any]
    ) -> ActionOutcome:
        name = str(params["name"]).strip() or "Layer"
        layer_type = str(params.get("layer_type") or "raster")
        if context.canvas.submit(AddLayerCommand(name, layer_type=layer_type)):
            return ActionOutcome.success(name)
        return ActionOutcome.failure("command_rejected")
