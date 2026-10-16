"""``text.outline_page``: convert every text object on the page to outlines (CT-25 command)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from loomstroke.core.actions import (
    ActionContext,
    ActionDescriptor,
    ActionEffect,
    ActionOutcome,
    ResolvedTarget,
)
from loomstroke.core.commands_text_outline import ConvertTextToOutlinesCommand

__all__ = ["TextOutlinePageAction"]


class TextOutlinePageAction:
    descriptor: ClassVar[ActionDescriptor] = ActionDescriptor(
        action_id="text.outline_page",
        version=1,
        title="Convert page text to outlines",
        category="text",
        target_kind="page",
        effects=(ActionEffect("mutation", "text objects become outline paths"),),
    )

    def execute(
        self, context: ActionContext, target: ResolvedTarget, params: Mapping[str, Any]
    ) -> ActionOutcome:
        from loomstroke.ui.font_outline_service import FontOutlineService  # noqa: PLC0415

        canvas = context.canvas
        getter = getattr(canvas, "get_vector_objects", None)
        if not callable(getter):
            return ActionOutcome.failure("canvas_unsupported")
        service = FontOutlineService(canvas)
        converted = 0
        for layer_id, vector in list(getter(include_hidden=True)):
            outline = service.outline(vector)
            if outline is None or outline.is_empty:
                continue
            if canvas.submit(ConvertTextToOutlinesCommand(vector.id, outline, layer_id=layer_id)):
                converted += 1
        return ActionOutcome.success(f"{converted} text object(s)")
