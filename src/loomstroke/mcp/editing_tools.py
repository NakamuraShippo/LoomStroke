"""Registration and input validation for the additive MCP editing surface."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from . import (
    advanced_fill_tools,
    book_master_tools,
    brush_asset_tools,
    brush_stamp_tools,
    brush_tools,
    canvas_tools,
    color_tools,
    comfyui_management_tools,
    document_tools,
    export_tools,
    fill_tools,
    filter_tools,
    generation_gallery_tools,
    generation_preset_tools,
    history_tools,
    layer_tools,
    llm_tools,
    page_template_tools,
    page_tools,
    palette_tools,
    sam_asset_tools,
    sam_export_tools,
    sam_layer_tools,
    sam_preparation_tools,
    sam_runtime_tools,
    sam_source_tools,
    sam_video_tools,
    selection_tools,
    shape_library_tools,
    text_tools,
    three_d_tools,
    transform_tools,
    vector_tools,
    view_tools,
)
from .editing_common import check_gui_thread, invalid

_MODULES = (
    sam_video_tools,
    comfyui_management_tools,
    export_tools,
    generation_gallery_tools,
    sam_preparation_tools,
    sam_runtime_tools,
    advanced_fill_tools,
    book_master_tools,
    canvas_tools,
    filter_tools,
    page_tools,
    page_template_tools,
    sam_export_tools,
    sam_layer_tools,
    brush_tools,
    document_tools,
    fill_tools,
    history_tools,
    layer_tools,
    color_tools,
    palette_tools,
    view_tools,
    brush_asset_tools,
    brush_stamp_tools,
    generation_preset_tools,
    llm_tools,
    sam_asset_tools,
    sam_source_tools,
    three_d_tools,
    selection_tools,
    shape_library_tools,
    text_tools,
    transform_tools,
    vector_tools,
)
TOOLS = tuple(spec for module in _MODULES for spec in module.TOOLS)
_DISPATCH = {spec.name: module.dispatch for module in _MODULES for spec in module.TOOLS}
_SCHEMAS = {spec.name: spec.input_schema for spec in TOOLS}
P0_TOOL_NAMES = frozenset(
    spec.name
    for module in (brush_tools, document_tools, fill_tools, history_tools, layer_tools)
    for spec in module.TOOLS
)


@lru_cache(maxsize=256)
def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(_SCHEMAS[name])


def _finite_values(value: Any, path: str = "arguments", depth: int = 0) -> None:
    if depth > 32:
        raise invalid("Input nesting exceeds the supported limit.", path)
    if isinstance(value, float) and not math.isfinite(value):
        raise invalid("Numbers must be finite.", path)
    if isinstance(value, Mapping):
        for key, item in value.items():
            _finite_values(item, f"{path}.{key}", depth + 1)
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _finite_values(item, f"{path}[{index}]", depth + 1)


def _validate(name: str, arguments: Mapping[str, Any]) -> None:
    _finite_values(arguments)
    error = next(_validator(name).iter_errors(arguments), None)
    if error is not None:
        path = ".".join(str(part) for part in error.absolute_path) or "arguments"
        # Do not copy the rejected instance into messages (paths, names or
        # unexpected keys can carry caller data).
        raise invalid(f"Invalid '{path}': schema rule '{error.validator}' failed.", path)


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    check_gui_thread()
    # Large point-array validation is pure Python; keep it off Qt's UI thread.
    await asyncio.to_thread(_validate, name, arguments)
    return await _DISPATCH[name](service, name, arguments)


def handles(name: str) -> bool:
    return name in _DISPATCH


def guard_existing_edit(service: Any, name: str, arguments: Mapping[str, Any]) -> None:
    from .editing_readiness import guard_existing_edit as guard  # noqa: PLC0415

    guard(service, name, arguments)
