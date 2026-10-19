"""Backdrop dependencies and output-to-source mapping for live layer styles."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from loomstroke.core.appearance.live_styles import layer_styles, style_signature
from loomstroke.ui.canvas.render_scene import _scene_3d_signature, _stable_signature

from .style_blend_shader import style_mode_id
from .transform_processor import WgpuTransformProcessor
from .types import WgpuDrawRegion


def needs_backdrop(layer: Any) -> bool:
    return any(
        style_mode_id(s.blend_mode)
        and s.kind
        in {
            "gaussian_blur",
            "drop_shadow",
            "outer_glow",
            "color_overlay",
            "gradient_overlay",
            "inner_shadow",
            "inner_glow",
            "three_d",
            "bevel_emboss",
            "stroke",
        }
        for s in layer_styles(layer).render_order()
    )


def backdrop_keys(scene: Any) -> dict[str, int]:
    """Hash each prefix once; upper siblings cannot invalidate a lower style."""
    result: dict[str, int] = {}

    def visit(nodes: Any, seed: int) -> int:
        value = seed
        for node in nodes:
            result[str(node.id)] = value
            children = visit(getattr(node, "children", ()), 0)
            image = getattr(node, "image", None)
            mask = getattr(node, "layer_mask", None)
            effect_mask = getattr(node, "effect_mask", None)
            vector = getattr(node, "vector_render_layer", None)
            value = hash(
                (
                    value,
                    str(node.id),
                    children,
                    image.cacheKey() if image is not None else 0,
                    (
                        mask.cacheKey()
                        if mask is not None and hasattr(mask, "cacheKey")
                        else repr(mask)
                    ),
                    getattr(node, "texture_version", 0),
                    getattr(node, "transform_version", 0),
                    getattr(node, "_preview_transform_version", 0),
                    style_signature(node),
                    repr(getattr(node, "transform", None)),
                    repr(getattr(node, "_preview_transform", None)),
                    getattr(node, "offset_x", 0),
                    getattr(node, "offset_y", 0),
                    getattr(node, "opacity", 1),
                    getattr(node, "visible", True),
                    str(getattr(node, "blend_mode", "normal")),
                    repr(getattr(node, "effect_params", None)),
                    getattr(node, "effect_type", None),
                    effect_mask.cacheKey() if effect_mask is not None else None,
                    getattr(node, "effect_mask_texture_version", 0),
                    _vector_key(vector),
                    _scene_3d_signature(getattr(node, "scene_3d", None)),
                    _stable_signature(getattr(node, "camera_3d", None)),
                    _stable_signature(getattr(node, "lighting_3d", None)),
                    getattr(node, "display_mode_3d", "solid"),
                    getattr(node, "mask_nearest", False),
                    getattr(node, "vector_raster_scale", 1),
                    tuple(getattr(node, "vector_raster_origin", (0, 0))),
                    getattr(node, "mask_enabled", True),
                    getattr(node, "clipping", False),
                )
            )
        return value

    visit(scene.layers, hash(tuple(scene.canvas_state.background_rgba)))
    return result


def _vector_key(payload: Any) -> Any:
    if payload is None:
        return None
    return (
        payload.layer_version,
        payload.object_order_version,
        payload.layer_transform,
        tuple(
            (
                obj.object_id,
                obj.geometry_version,
                obj.style_version,
                obj.transform_version,
                obj.resource_version,
                obj.metadata_signature,
                obj.is_preview,
            )
            for obj in payload.objects
        ),
    )


@dataclass(frozen=True)
class StyleBackdrop:
    texture: Any
    mapping: tuple[float, ...]
    key: Any
    underlay: Any = None


@contextmanager
def clip_backdrop_scope(compositor: Any, members: Any, target: Any) -> Iterator[None]:
    previous = getattr(compositor, "_style_clip_backdrops", {})
    compositor._style_clip_backdrops = {**previous, **{str(m.id): target for m in members}}
    try:
        yield
    finally:
        compositor._style_clip_backdrops = previous


def style_backdrop(
    compositor: Any,
    scene: Any,
    layer: Any,
    target: Any,
    *,
    scale: int = 1,
    origin: tuple[int, ...] = (0, 0),
    group: bool = False,
) -> StyleBackdrop | None:
    if not needs_backdrop(layer):
        return None
    view = compositor._view_inverse or (1, 0, 0, 1, 0, 0)
    affine = WgpuTransformProcessor._explicit_affine(layer) if not group else None
    affine = affine or (
        1,
        0,
        0,
        1,
        0 if group else getattr(layer, "offset_x", 0),
        0 if group else getattr(layer, "offset_y", 0),
    )
    inverse = WgpuTransformProcessor._inverse_affine(affine)
    if inverse is None:
        return None
    a, b, c, d, tx, ty = inverse
    va, vb, vc, vd, vx, vy = view
    mapping = (
        scale * (a * va + b * vc),
        scale * (a * vb + b * vd),
        scale * (c * va + d * vc),
        scale * (c * vb + d * vd),
        scale * (a * vx + b * vy + tx - origin[0]),
        scale * (c * vx + d * vy + ty - origin[1]),
    )
    external = getattr(compositor, "_style_clip_backdrops", {}).get(str(layer.id))
    return StyleBackdrop(
        external or target,
        mapping,
        (
            compositor._style_backdrop_keys.get(str(layer.id)),
            mapping,
            target.width,
            target.height,
            external is not None,
        ),
        target if external is not None else None,
    )


def backdrop_region(backdrop: StyleBackdrop) -> WgpuDrawRegion:
    size = (float(backdrop.texture.width), float(backdrop.texture.height))
    return WgpuDrawRegion(
        dest_origin=(0.0, 0.0),
        dest_size=size,
        uv_origin=(0.0, 0.0),
        uv_size=(1.0, 1.0),
        src_size=size,
    )
