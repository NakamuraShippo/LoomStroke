"""Decorate bounded native tiles without separating the original inks again."""

from __future__ import annotations

import math
from copy import copy
from functools import lru_cache
from typing import Any

import numpy as np
from PySide6.QtCore import QRect

from loomstroke.core.appearance import AppearanceRegistry
from loomstroke.core.appearance.live_styles import layer_styles, style_padding
from loomstroke.core.native_cmyk.affine import placement
from loomstroke.core.native_cmyk.color_transform import CmykColorTransform

from .layer_style_evaluator import evaluate_styles
from .native_layer_style_shader import NATIVE_LAYER_STYLE_WGSL
from .relief_style_uniforms import EMPTY_RELIEF
from .style_backdrop import needs_backdrop


@lru_cache(maxsize=256)
def _ink_color(profile: bytes, color: tuple[int, ...]) -> tuple[float, ...]:
    values = np.array([[color]], np.uint8)
    inks, _ = CmykColorTransform.from_rgb(values, profile)
    return tuple(float(value) / np.iinfo(inks.dtype).max for value in inks[0, 0])


def _source_geometry(node: Any, rect: QRect, padding: int, bounds: QRect) -> tuple[Any, QRect, Any]:
    expanded = rect.adjusted(-padding, -padding, padding, padding).intersected(bounds)
    if node.native_cmyk is None:
        return node, expanded, None
    inverse = placement(node).inverse()
    if inverse is None:
        raise ValueError("Layer styles require an invertible native transform")
    points = [
        inverse.apply(x, y)
        for x in (rect.left(), rect.right() + 1)
        for y in (rect.top(), rect.bottom() + 1)
    ]
    x0, y0 = [math.floor(min(p[i] for p in points)) - padding - 2 for i in (0, 1)]
    x1, y1 = [math.ceil(max(p[i] for p in points)) + padding + 2 for i in (0, 1)]
    height, width = node.native_cmyk.inks.shape[:2]
    expanded = QRect(x0, y0, x1 - x0, y1 - y0).intersected(
        QRect(-padding - 2, -padding - 2, width + 2 * padding + 4, height + 2 * padding + 4)
    )
    source = copy(node)
    source.offset_x = source.offset_y = 0
    source.transform = source._preview_transform = None
    return source, expanded, inverse


class NativeLayerStyleProcessor:
    def __init__(self, bundle: Any) -> None:
        self.bundle = bundle
        shader = bundle.device.create_shader_module(code=NATIVE_LAYER_STYLE_WGSL)
        self.pipeline = bundle.device.create_compute_pipeline(
            layout="auto", compute={"module": shader, "entry_point": "main"}
        )
        self.registry = AppearanceRegistry(include_adjustments=False)

    def render(self, compositor: Any, node: Any, depth: int, backdrop: Any = None) -> Any:
        stack = layer_styles(node)
        document = backdrop is not None and needs_backdrop(node)
        original_rect, original_count = compositor._rect, compositor._count
        padding = style_padding(stack)
        if node.native_cmyk is not None:
            height, width = node.native_cmyk.inks.shape[:2]
        else:
            width, height = compositor._canvas_bounds.width(), compositor._canvas_bounds.height()
        # Source halo is captured independently of the requested output tile.
        source_node, expanded, inverse = _source_geometry(
            node, original_rect, padding, compositor._canvas_bounds
        )
        if expanded.isEmpty():
            return None
        compositor._rect, compositor._count = expanded, expanded.width() * expanded.height()
        try:
            base = compositor._unstyled_source(source_node, depth)
            if base is None:
                return None
            count = compositor._count
            needs_ranges = any(
                s.kind in {"stroke", "outer_glow", "inner_glow", "bevel_emboss"}
                for s in stack.enabled()
            )
            ranges = compositor._buffer(np.zeros(count * 8 if needs_ranges else 1, np.float32))
            free: list[Any] = []

            def target(pixels: int = count) -> Any:
                result = next((b for b in free if b.size >= pixels * 20), None)
                if result is not None:
                    free.remove(result)
                    return result
                return compositor._buffer(np.zeros(pixels * 5, np.float32))

            def dispatch(
                src: Any,
                op: int,
                radius: int = 0,
                mode: int = 0,
                geometry: tuple[int, ...] = (0, 0, 0, 0),
                color: tuple[float, ...] = (0, 0, 0, 0),
                opacity: float = 0.0,
                previous: Any = None,
                mask: Any = None,
                level: int = 0,
                crop: tuple[int, ...] = (0, 0, 0, 0),
                output: Any = None,
                coverage: Any = None,
                style: tuple[float, ...] = (0, 0, 0, 0),
                secondary: tuple[float, ...] = (0, 0, 0, 0),
                lighting: tuple[float, ...] = (0, 0, 0, 0),
                material: tuple[float, ...] = (0, 0, 0, 0),
                relief: tuple[float, ...] = EMPTY_RELIEF,
                blend: int = 0,
                underlay: Any = None,
            ) -> Any:
                mapped = document and op in {4, 9, 11, 12, 13, 14}
                output = output or target(original_count if mapped else count)
                a, b, c, d, tx, ty = (
                    (inverse.a, inverse.b, inverse.c, inverse.d, inverse.tx, inverse.ty)
                    if inverse
                    else (1, 0, 0, 1, 0, 0)
                )
                params = np.array(
                    [
                        expanded.width(),
                        expanded.height(),
                        op,
                        radius,
                        *geometry,
                        *color,
                        mode,
                        level,
                        opacity,
                        0,
                        *crop,
                        *(
                            (inverse.a, inverse.b, inverse.c, inverse.d)
                            if inverse
                            else (1, 0, 0, 1)
                        ),
                        *((inverse.tx, inverse.ty) if inverse else (0, 0)),
                        expanded.x(),
                        expanded.y(),
                        *original_rect.getRect(),
                        *style,
                        *secondary,
                        *lighting,
                        *material,
                        expanded.x(),
                        expanded.y(),
                        width,
                        height,
                        blend,
                        int(mapped),
                        int(op == 4 and mode == 0),
                        0,
                        *relief,
                        a,
                        b,
                        a * original_rect.x() + b * original_rect.y() + tx - expanded.x() - 0.5,
                        0,
                        c,
                        d,
                        c * original_rect.x() + d * original_rect.y() + ty - expanded.y() - 0.5,
                        0,
                        original_rect.width(),
                        original_rect.height(),
                        int(mapped),
                        int(underlay is not None),
                    ],
                    np.float32,
                )
                uniform = compositor._batch.upload(params, self.bundle.wgpu.BufferUsage.UNIFORM)
                bindings = [
                    src,
                    previous or src,
                    mask or src,
                    output,
                    ranges,
                    uniform,
                    coverage or src,
                    backdrop if mapped else src,
                    underlay or src,
                ]
                group = self.bundle.device.create_bind_group(
                    layout=self.pipeline.get_bind_group_layout(0),
                    entries=[
                        {"binding": i, "resource": {"buffer": b}} for i, b in enumerate(bindings)
                    ],
                )
                compute = compositor._encoder.begin_compute_pass()
                compute.set_pipeline(self.pipeline)
                compute.set_bind_group(0, group)
                compute.dispatch_workgroups(
                    ((original_count if op == 6 or mapped else count) + 63) // 64
                )
                compute.end()
                return output

            accumulator = dispatch(base, 13) if document else target()

            def dilate(src: Any, radius: int, *, erosion: bool = False) -> Any:
                for level in range((2 * radius + 1).bit_length()):
                    dispatch(src, 1, mode=int(erosion), level=level, output=accumulator)
                return dispatch(src, 2, radius, mode=int(erosion))

            accumulator = evaluate_styles(
                stack,
                self.registry,
                base,
                base,
                accumulator,
                render=dispatch,
                dilate=dilate,
                release=free.append,
                convert_color=lambda color: _ink_color(compositor._native_profile, tuple(color)),
                extract_alpha=lambda src: src,
                project=(lambda src: dispatch(src, 12)) if document else None,
            )
            if document:
                return accumulator
            cropped = compositor._buffer(np.zeros(original_count * 5, np.float32))
            dispatch(
                accumulator,
                6 if inverse is not None else 5,
                crop=(
                    original_rect.x() - expanded.x(),
                    original_rect.y() - expanded.y(),
                    original_rect.width(),
                    original_rect.height(),
                ),
                output=cropped,
            )
            return cropped
        finally:
            compositor._rect, compositor._count = original_rect, original_count
