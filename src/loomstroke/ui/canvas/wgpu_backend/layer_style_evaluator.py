"""Fixed style ordering shared by straight RGB textures and native ink buffers."""

from collections.abc import Callable
from typing import Any

from loomstroke.core.appearance import AppearanceRegistry, LayerStyleSpec, LayerStyleStack

from .extended_style_evaluator import blurred_source, extended_style
from .style_blend_shader import style_mode_id


def _mask(
    style: LayerStyleSpec,
    params: dict[str, Any],
    source: Any,
    render: Callable[..., Any],
    dilate: Callable[..., Any],
    release: Callable[[Any], None],
    scale: int,
) -> Any:
    if style.kind == "drop_shadow":
        radius = params["blur"] * scale
        mask = render(
            source,
            3,
            radius,
            geometry=(
                1,
                0,
                round(params["offset_x"]) * scale,
                round(params["offset_y"]) * scale,
            ),
        )
        result = render(mask, 3, radius, geometry=(0, 1, 0, 0))
        release(mask)
        return result
    mask = dilate(source, params["size"] * scale)
    if style.kind == "outer_glow":
        radius = max(1, params["size"] // 2) * scale
        for axis in ((1, 0, 0, 0), (0, 1, 0, 0)):
            result = render(mask, 3, radius, geometry=axis)
            release(mask)
            mask = result
    return mask


def evaluate_styles(
    stack: LayerStyleStack,
    registry: AppearanceRegistry,
    base: Any,
    alpha_source: Any,
    accumulator: Any,
    *,
    render: Callable[..., Any],
    dilate: Callable[..., Any],
    release: Callable[[Any], None],
    convert_color: Callable[[list[int]], tuple[float, ...]],
    extract_alpha: Callable[[Any], Any],
    scale: int = 1,
    project: Callable[[Any], Any] | None = None,
) -> Any:
    base, alpha_source, projected = blurred_source(
        stack,
        registry,
        base,
        alpha_source,
        render=render,
        extract_alpha=extract_alpha,
        release=release,
        scale=scale,
        projected=project(base) if project else None,
    )
    overlaid, above = projected if projected is not None else base, False
    for style in stack.render_order():
        if registry.get("style", style.kind) is None:
            continue
        params, _ = registry.validate("style", style.kind, style.params)
        if style.kind == "gaussian_blur":
            continue
        if style.kind in {
            "inner_shadow",
            "inner_glow",
            "gradient_overlay",
            "bevel_emboss",
            "three_d",
        }:
            result = extended_style(
                style,
                params,
                base,
                alpha_source,
                overlaid,
                render=render,
                dilate=dilate,
                release=release,
                convert_color=convert_color,
                scale=scale,
            )
            if overlaid is not base:
                release(overlaid)
            overlaid = result
            continue
        color = convert_color(params["color"])
        opacity = params["color"][3] / 255 * style.opacity * params["opacity"]
        if style.kind == "stroke" and not above:
            result = render(overlaid, 4, previous=accumulator)
            release(accumulator)
            accumulator, above = result, True
        if style.kind == "color_overlay":
            result = render(
                base,
                4,
                mode=1,
                previous=overlaid,
                color=color,
                opacity=opacity,
                blend=style_mode_id(style.blend_mode),
            )
            if overlaid is not base:
                release(overlaid)
            overlaid = result
            continue
        mask = _mask(style, params, alpha_source, render, dilate, release, scale)
        result = render(
            base,
            4,
            mode=3 if style.kind == "stroke" else 2,
            previous=accumulator,
            mask=mask,
            color=color,
            opacity=opacity,
            blend=style_mode_id(style.blend_mode),
        )
        release(accumulator)
        release(mask)
        accumulator = result
    if not above:
        result = render(overlaid, 4, previous=accumulator)
        release(accumulator)
        accumulator = result
    return accumulator
