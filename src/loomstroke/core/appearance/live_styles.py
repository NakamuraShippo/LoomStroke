"""Normalized live layer styles shared by snapshots and GPU cache keys."""

from __future__ import annotations

import math
from typing import Any

from .appearance_codec import AppearanceLayerMetadataCodec
from .appearance_registry import AppearanceRegistry
from .layer_style_stack import LayerStyleStack


def layer_styles(layer: Any) -> LayerStyleStack:
    stack = getattr(layer, "style_stack", None)
    if isinstance(stack, LayerStyleStack):
        return stack
    return AppearanceLayerMetadataCodec().read_styles(getattr(layer, "metadata", None))


def style_signature(layer: Any) -> str:
    stack = layer_styles(layer)
    return repr(stack.to_dict()) if stack.enabled() else ""


def style_padding(stack: LayerStyleStack) -> int:
    registry = AppearanceRegistry(include_adjustments=False)
    padding = 0
    blur_padding = 0
    for style in stack.enabled():
        params, _ = registry.validate("style", style.kind, style.params)
        if style.kind in {"drop_shadow", "inner_shadow"}:
            padding = max(
                padding,
                math.ceil(max(abs(params["offset_x"]), abs(params["offset_y"]))) + params["blur"],
            )
        elif style.kind == "outer_glow":
            padding = max(padding, params["size"] + max(1, params["size"] // 2))
        elif style.kind == "stroke":
            padding = max(padding, params["size"])
        elif style.kind == "inner_glow":
            padding = max(padding, params["size"] + max(1, params["size"] // 2))
        elif style.kind in {"bevel_emboss", "three_d"}:
            padding = max(padding, params["size"] + params["soften"] + 1)
        elif style.kind == "gaussian_blur" and style.opacity * params["opacity"] > 0:
            blur_padding += params["radius"]
    return padding + blur_padding


def style_halos(layers: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for layer in layers:
        parent = getattr(layer, "parent_id", None)
        inherited = result.get(parent, 0) if isinstance(parent, str) else 0
        result[layer.id] = inherited + document_style_padding(layer)
    return result


def document_style_padding(layer: Any) -> int:
    stack = layer_styles(layer)
    if not stack.enabled():
        return 0
    padding = style_padding(stack) + 2
    transform = getattr(layer, "_preview_transform", None) or getattr(layer, "transform", None)
    to_affine = getattr(transform, "to_affine", None)
    if padding and callable(to_affine):
        affine = to_affine()
        scale = max(abs(affine.a) + abs(affine.b), abs(affine.c) + abs(affine.d))
        if math.isfinite(scale):
            padding = math.ceil(padding * scale) + 2
    return padding
