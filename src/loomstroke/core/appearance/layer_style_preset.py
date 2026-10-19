"""A named, independent copy of an entire layer style stack."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .appearance_registry import AppearanceRegistry
from .layer_style_stack import LayerStyleStack


def _check_parameter_tree(raw: dict[str, Any]) -> None:
    pending = [(raw, 0)]
    while pending:
        item, depth = pending.pop()
        if (
            depth > 32
            or not isinstance(item, (dict, list, tuple, str, bool, int, float, type(None)))
            or (isinstance(item, float) and not math.isfinite(item))
        ):
            raise ValueError("Invalid preset parameter data")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("Invalid preset parameter key")
            pending.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend((v, depth + 1) for v in item)


@dataclass(frozen=True, slots=True)
class LayerStylePreset:
    preset_id: str
    name: str
    stack: LayerStyleStack

    def copied_stack(self) -> LayerStyleStack:
        return deepcopy(self.stack)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.preset_id, "name": self.name, "stack": deepcopy(self.stack.to_dict())}

    @classmethod
    def from_dict(cls, value: Any) -> LayerStylePreset:
        if not isinstance(value, dict):
            raise ValueError("Invalid layer style preset")
        identifier = value.get("id")
        if not isinstance(identifier, str) or str(UUID(identifier)) != identifier:
            raise ValueError("Invalid preset ID")
        name = value.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 128:
            raise ValueError("Invalid preset name")
        raw = value.get("stack")
        if not isinstance(raw, dict) or not isinstance(raw.get("styles"), list):
            raise ValueError("Invalid preset stack")
        if len(raw["styles"]) > LayerStyleStack.MAX_STYLES:
            raise ValueError("Too many styles")
        _check_parameter_tree(raw)
        stack = LayerStyleStack.from_dict(deepcopy(raw))
        ids = {style.style_id for style in stack.styles}
        if len(stack.styles) != len(raw["styles"]) or len(ids) != len(stack.styles):
            raise ValueError("Invalid or duplicate style IDs")
        registry = AppearanceRegistry(include_adjustments=False)
        for style in stack.styles:
            if registry.get("style", style.kind) is not None:
                _params, issues = registry.validate("style", style.kind, style.params)
                if any(issue.is_error for issue in issues):
                    raise ValueError("Invalid style parameters")
        return cls(identifier, name.strip(), stack)
