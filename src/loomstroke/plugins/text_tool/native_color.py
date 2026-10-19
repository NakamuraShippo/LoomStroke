"""Capture the shared native colour when a new editable text object starts."""

from dataclasses import replace
from typing import Any, cast

from loomstroke.core.native_cmyk.vector_paints import PAINTS_KEY, capture_paint
from loomstroke.plugins.vector_tools.common import VectorObject

from .common import ensure_text_vector_object


def new_ink_text_object(
    tool: Any, content: str, rect: tuple[float, float, float, float], word_wrap: bool
) -> VectorObject | None:
    state = getattr(tool.host_api, "color_state", None)
    paint = capture_paint(getattr(state, "current", None))
    if paint is None:
        return None
    settings = replace(
        tool._settings(), color_rgba=cast("tuple[int, int, int, int]", tuple(paint["rgba"]))
    )
    obj = ensure_text_vector_object(
        object_id=None,
        content=content,
        rect=rect,
        settings=settings,
        word_wrap=word_wrap,
    )
    return replace(obj, meta={**obj.meta, PAINTS_KEY: {"version": 1, "text": paint}})
