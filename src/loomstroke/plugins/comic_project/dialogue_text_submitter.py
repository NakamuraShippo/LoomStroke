"""Create a text object from a dialogue plan through the canvas command path (CT-17 §8).

Mirrors the Text Tool's own creation: a text vector layer (the active one
if it is a text layer, else a new one), ``ensure_text_vector_object`` for
the object, ``VectorCreateCommand`` submitted via ``canvas.submit`` — one
undoable step.  Returns the new object id only when the command was
accepted, so the caller commits the dialogue link after success.
"""

from __future__ import annotations

import logging
from typing import Any

from loomstroke.core.comic_project.dialogue_apply_plan import DialogueApplyPlan
from loomstroke.plugins.text_tool.common import TextToolSettings, ensure_text_vector_object
from loomstroke.plugins.vector_tools.common import VectorCreateCommand

logger = logging.getLogger(__name__)

__all__ = ["DialogueTextSubmitter"]


class DialogueTextSubmitter:
    LAYER_NAME: str = "Dialogue"

    def __init__(self, host_api: Any) -> None:
        self._host = host_api

    def create(self, plan: DialogueApplyPlan) -> str | None:
        canvas = getattr(self._host, "canvas", None)
        submit = getattr(canvas, "submit", None)
        if canvas is None or not callable(submit):
            return None
        layer_id, created_layer_id = self._ensure_text_layer(canvas)
        if layer_id is None:
            return None
        settings = TextToolSettings(
            word_wrap=True,
            font_size_pt=float(plan.font_size_pt),
            vertical=bool(plan.vertical),
        )
        new_obj = ensure_text_vector_object(
            object_id=None,
            content=plan.content,
            rect=plan.rect,
            settings=settings,
            word_wrap=True,
        )
        command = VectorCreateCommand(
            new_obj,
            layer_id=layer_id,
            merge_key=plan.merge_key,
            created_layer_id=created_layer_id,
        )
        try:
            accepted = bool(submit(command, merge_key=plan.merge_key))
        except Exception:
            logger.warning("dialogue text creation failed", exc_info=True)
            return None
        if not accepted:
            return None
        return str(getattr(new_obj, "id", "") or "") or None

    def _ensure_text_layer(self, canvas: Any) -> tuple[str | None, str | None]:
        layers = []
        get_layers = getattr(canvas, "get_layers", None)
        if callable(get_layers):
            try:
                layers = list(get_layers() or [])
            except Exception:
                layers = []
        active_id = None
        get_active = getattr(canvas, "get_active_layer_id", None)
        if callable(get_active):
            try:
                active_id = get_active()
            except Exception:
                active_id = None
        for layer in layers:
            if (
                getattr(layer, "id", None) == active_id
                and getattr(layer, "layer_type", "") == "vector"
                and getattr(layer, "vector_kind", "") == "text"
            ):
                return str(layer.id), None
        add_vector_layer = getattr(canvas, "add_vector_layer", None)
        if callable(add_vector_layer):
            try:
                ref = add_vector_layer(self.LAYER_NAME, vector_kind="text")
                layer_id = getattr(ref, "id", None)
                if isinstance(layer_id, str):
                    return layer_id, layer_id
            except Exception:
                logger.debug("add_vector_layer failed for dialogue text", exc_info=True)
        add_layer = getattr(canvas, "add_layer", None)
        if callable(add_layer):
            try:
                ref = add_layer(self.LAYER_NAME, layer_type="vector", vector_kind="text")
                layer_id = getattr(ref, "id", None)
                if isinstance(layer_id, str):
                    return layer_id, layer_id
            except Exception:
                logger.debug("add_layer failed for dialogue text", exc_info=True)
        return None, None
