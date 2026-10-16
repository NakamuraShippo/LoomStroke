"""Validate MCP AI Selection prompt arguments before the compatibility parser."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from loomstroke.ai_selection.data_model import Sam3Prompt

from .types import McpStructuredError, ReasonCode

_PROMPT_MODES = frozenset({"text", "point", "box", "mask", "exemplar", "mixed"})


def parse_sam3_prompt_arguments(arguments: Mapping[str, Any]) -> Sam3Prompt:
    """Preserve valid defaults and aliases while rejecting malformed inputs."""
    value = arguments.get("prompt")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise _invalid_prompt("'prompt' must not be empty.")
        return Sam3Prompt(mode="text", text=text)
    if not isinstance(value, Mapping):
        raise _invalid_prompt("'prompt' is required.")

    mode = value.get("mode")
    if mode is None:
        mode = "text"
    if not isinstance(mode, str) or mode not in _PROMPT_MODES:
        raise _invalid_prompt("'prompt.mode' must be a supported prompt mode.")
    normalized = {
        "mode": mode,
        "text": _optional_text(value.get("text"), "text"),
        "points": _geometry(value.get("points"), "points"),
        "boxes": _geometry(value.get("boxes"), "boxes"),
        "mask_asset_id": _optional_text(
            value.get("maskAssetId", value.get("mask_asset_id")), "maskAssetId"
        ),
        "exemplar_asset_ids": _exemplar_ids(
            value.get("exemplarAssetIds", value.get("exemplar_asset_ids"))
        ),
        "negative_text": _optional_text(
            value.get("negativeText", value.get("negative_text")), "negativeText"
        ),
        "locale": _optional_text(value.get("locale"), "locale"),
        "normalized_text": _optional_text(
            value.get("normalizedText", value.get("normalized_text")), "normalizedText"
        ),
    }
    prompt = Sam3Prompt.from_dict(normalized)
    _validate_active_boxes(prompt)
    if not (
        (prompt.text and prompt.text.strip())
        or (prompt.normalized_text and prompt.normalized_text.strip())
        or prompt.points
        or prompt.boxes
        or prompt.mask_asset_id
        or prompt.exemplar_asset_ids
    ):
        raise _invalid_prompt("'prompt' must include text, points, boxes, or mask references.")
    return prompt


def _validate_active_boxes(prompt: Sam3Prompt) -> None:
    if prompt.mode not in {"box", "exemplar"} and not (
        prompt.mode == "mixed" and not prompt.points
    ):
        return
    for index, box in enumerate(prompt.boxes):
        if box.width <= 0 or box.height <= 0:
            raise _invalid_prompt(f"'prompt.boxes[{index}]' must have positive width and height.")


def _optional_text(value: Any, field: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise _invalid_prompt(f"'prompt.{field}' must be a string.")
    return value


def _geometry(value: Any, field: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        raise _invalid_prompt(f"'prompt.{field}' must be an array of objects.")
    coordinates = ("x", "y") if field == "points" else ("x", "y", "width", "height")
    items: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        path = f"prompt.{field}[{index}]"
        if not isinstance(item, Mapping):
            raise _invalid_prompt(f"'{path}' must be an object.")
        normalized: dict[str, Any] = {
            key: _finite_number(item.get(key, 0.0), f"{path}.{key}") for key in coordinates
        }
        label = item.get("label")
        if field == "points":
            if label is None:
                label = "positive"
            if not isinstance(label, str) or label not in {"positive", "negative"}:
                raise _invalid_prompt(f"'{path}.label' must be positive or negative.")
        elif label is not None and not isinstance(label, str):
            raise _invalid_prompt(f"'{path}.label' must be a string.")
        normalized["label"] = label
        items.append(normalized)
    return items


def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _invalid_prompt(f"'{path}' must be a finite number.")
    try:
        number = float(value)
    except OverflowError as exc:
        raise _invalid_prompt(f"'{path}' must be a finite number.") from exc
    if not math.isfinite(number):
        raise _invalid_prompt(f"'{path}' must be a finite number.")
    return number


def _exemplar_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list | tuple) or any(not isinstance(item, str) for item in value):
        raise _invalid_prompt("'prompt.exemplarAssetIds' must be an array of strings.")
    return list(value)


def _invalid_prompt(message: str) -> McpStructuredError:
    return McpStructuredError(
        ReasonCode.INVALID_ARGUMENT,
        message,
        details={"argument": "prompt"},
        code=-32602,
    )
