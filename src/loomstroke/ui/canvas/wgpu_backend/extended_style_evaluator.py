"""Additional style passes, shared by RGB textures and native CMYK buffers."""

import math
from collections.abc import Callable
from typing import Any

from loomstroke.core.appearance import AppearanceRegistry, LayerStyleSpec, LayerStyleStack

from .relief_style_uniforms import relief_uniforms
from .style_blend_shader import style_mode_id


def blurred_source(
    stack: LayerStyleStack,
    registry: AppearanceRegistry,
    base: Any,
    alpha_source: Any,
    *,
    render: Callable[..., Any],
    extract_alpha: Callable[[Any], Any],
    release: Callable[[Any], None],
    scale: int,
    projected: Any = None,
) -> tuple[Any, Any, Any]:
    changed = False
    for style in stack.render_order():
        if style.kind != "gaussian_blur":
            continue
        params, _ = registry.validate("style", style.kind, style.params)
        radius, opacity = params["radius"] * scale, style.opacity * params["opacity"]
        if radius <= 0 or opacity <= 0:
            continue
        horizontal = render(base, 7, radius, geometry=(1, 0, 0, 0))
        blurred = render(horizontal, 7, radius, geometry=(0, 1, 0, 0))
        release(horizontal)
        if projected is not None:
            result = render(
                blurred,
                14,
                previous=projected,
                opacity=opacity,
                blend=style_mode_id(style.blend_mode),
            )
            release(projected)
            projected = result
        if opacity < 1 or style_mode_id(style.blend_mode):
            mixed = render(
                blurred, 8, previous=base, opacity=opacity, blend=style_mode_id(style.blend_mode)
            )
            release(blurred)
            base = mixed
        else:
            base = blurred
        changed = True
    return base, extract_alpha(base) if changed else alpha_source, projected


def _mask_blur(
    source: Any, radius: int, render: Callable[..., Any], release: Callable[[Any], None]
) -> Any:
    if radius <= 0:
        return source
    horizontal = render(source, 10, radius, geometry=(1, 0, 0, 0))
    result = render(horizontal, 10, radius, geometry=(0, 1, 0, 0))
    release(horizontal)
    return result


def _four(color: tuple[float, ...]) -> tuple[float, ...]:
    return (*color, 0.0, 0.0, 0.0, 0.0)[:4]


def extended_style(
    style: LayerStyleSpec,
    params: dict[str, Any],
    base: Any,
    alpha_source: Any,
    old: Any,
    *,
    render: Callable[..., Any],
    dilate: Callable[..., Any],
    release: Callable[[Any], None],
    convert_color: Callable[[list[int]], tuple[float, ...]],
    scale: int,
) -> Any:
    opacity = style.opacity * params["opacity"]
    if style.kind == "gradient_overlay":
        return render(
            base,
            9,
            mode=int(params["shape"] == "radial"),
            previous=old,
            color=convert_color(params["color_start"]),
            opacity=opacity,
            secondary=_four(convert_color(params["color_end"])),
            style=(
                math.radians(params["angle"]),
                params["scale"],
                params["center_x"],
                params["center_y"],
            ),
            lighting=(params["color_start"][3] / 255, params["color_end"][3] / 255, 0, 0),
            blend=style_mode_id(style.blend_mode),
        )
    if style.kind in {"inner_glow", "inner_shadow"}:
        if style.kind == "inner_shadow":
            horizontal = render(
                alpha_source,
                3,
                params["blur"] * scale,
                geometry=(
                    1,
                    0,
                    round(params["offset_x"]) * scale,
                    round(params["offset_y"]) * scale,
                ),
            )
            mask = render(horizontal, 3, params["blur"] * scale, geometry=(0, 1, 0, 0))
            release(horizontal)
        else:
            eroded = dilate(alpha_source, params["size"] * scale, erosion=True)
            mask = render(eroded, 3, max(1, params["size"] // 2) * scale, geometry=(1, 0, 0, 0))
            result = render(mask, 3, max(1, params["size"] // 2) * scale, geometry=(0, 1, 0, 0))
            release(eroded)
            release(mask)
            mask = result
        result = render(
            base,
            4,
            mode=4,
            previous=old,
            mask=mask,
            color=convert_color(params["color"]),
            opacity=opacity * params["color"][3] / 255,
            blend=style_mode_id(style.blend_mode),
        )
        release(mask)
        return result
    height = _mask_blur(alpha_source, params["size"] * scale, render, release)
    softened = _mask_blur(height, params["soften"] * scale, render, release)
    if softened is not height:
        release(height)
    three_d = style.kind == "three_d"
    mode = ("inner", "outer", "emboss", "pillow").index(params.get("type", "inner"))
    coverage = dilate(alpha_source, params["size"] * scale) if mode else base
    result = render(
        base,
        11,
        previous=old,
        mask=softened,
        coverage=coverage,
        geometry=(mode, int(params.get("invert", False)), 0, 0),
        color=convert_color(params["highlight_color"]),
        secondary=_four(convert_color(params["shadow_color"])),
        style=(
            params["depth"] * params["size"] * scale,
            math.radians(params["angle"]),
            math.radians(params["elevation"]),
            opacity,
        ),
        lighting=(
            params["highlight_color"][3] / 255,
            params["shadow_color"][3] / 255,
            params.get("ambient", 0),
            params.get("diffuse", 0),
        ),
        material=(params.get("specular", 0), params.get("shininess", 1), float(three_d), 0),
        relief=relief_uniforms(params, convert_color),
        blend=style_mode_id(style.blend_mode),
    )
    release(softened)
    if coverage is not base:
        release(coverage)
    return result
