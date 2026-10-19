"""Bake only the foreground while retaining the scene used by style modes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import numpy as np

from loomstroke.core.appearance.add_appearance_layer_command import AddAppearanceLayerCommand
from loomstroke.core.appearance.add_native_appearance_layer_command import (
    AddNativeAppearanceLayerCommand,
)
from loomstroke.core.commands_layer_blend_mode import SetLayerBlendModeCommand
from loomstroke.core.commands_layer_image_replace import ReplaceLayerImageCommand
from loomstroke.core.commands_layer_transform import LayerVisibilityCommand
from loomstroke.core.commands_layer_tree import ReparentLayerCommand, ToggleClippingCommand
from loomstroke.core.mask_coverage import mask_coverage_alpha8
from loomstroke.core.native_cmyk.pixels import NativeCmykPixels

from .canvas.native_cmyk_scene_renderer import NativeCmykSceneRenderer
from .canvas.page_scene_snapshot import flatten_render_layers
from .canvas.render_scene import build_render_scene_snapshot
from .canvas.wgpu_backend import WgpuOffscreenRenderer


def _capture_scene(canvas: Any, identifier: str) -> Any:
    scene = build_render_scene_snapshot(canvas)

    def visit(node: Any) -> tuple[Any, bool]:
        children = [visit(child) for child in node.children]
        found = node.id == identifier or any(found for _, found in children)
        return (
            replace(
                node,
                children=tuple(child for child, _ in children),
                visible=True if found else node.visible,
                opacity=1.0 if found and node.opacity <= 0 else node.opacity,
            ),
            found,
        )

    roots = tuple(visit(node)[0] for node in scene.layers)
    flat, parents = flatten_render_layers(roots)
    return replace(
        scene,
        layers=roots,
        flat_layers=tuple(flat),
        parent_ids=tuple(parents),
        channel_view=None,
        native_selection_preview=None,
    )


def _rgb_foreground(canvas: Any, layer: Any) -> np.ndarray:
    renderer = WgpuOffscreenRenderer()
    processor = renderer._compositor.layer_style_processor
    processor.capture_id = str(layer.id)
    try:
        target, _, _ = renderer._compositor.render(_capture_scene(canvas, layer.id))
        target.texture.destroy()
        texture = processor.captured
        if texture is None:
            raise ValueError("The styled layer could not be captured")
        pitch = ((texture.width * 8 + 255) // 256) * 256
        raw = renderer._bundle.queue.read_texture(
            {"texture": texture.texture},
            {"offset": 0, "bytes_per_row": pitch, "rows_per_image": texture.height},
            (texture.width, texture.height, 1),
        )
        values = np.frombuffer(raw, np.float16).reshape(texture.height, pitch // 2)
        rgba = (
            values[:, : texture.width * 4]
            .reshape(texture.height, texture.width, 4)
            .astype(np.float32)
        )
        rgba[..., 3] *= max(0.0, min(1.0, float(layer.opacity)))
        if layer.layer_mask is not None and layer.mask_enabled:
            if isinstance(layer.layer_mask, np.ndarray):
                coverage = layer.layer_mask
            else:
                image = mask_coverage_alpha8(layer.layer_mask)
                coverage = np.frombuffer(image.constBits(), np.uint8).reshape(
                    image.height(), image.bytesPerLine()
                )[:, : image.width()]
            rgba[..., 3] *= coverage / 255
        return np.rint(np.clip(rgba, 0, 1) * 255).astype(np.uint8)
    finally:
        renderer.release()


def bake_backdrop_styles(service: Any, layer: Any, *, keep_source: bool) -> str | None:
    canvas = service.canvas
    native = any(getattr(n, "native_cmyk", None) is not None for n in canvas._flatten_layers())
    image = None
    command: AddAppearanceLayerCommand
    if native:
        factory: Callable[[], Any] = NativeCmykSceneRenderer
        renderer = factory()
        renderer.compositor.capture_id = str(layer.id)
        try:
            pixels = renderer.render(_capture_scene(canvas, layer.id))
            if layer.opacity <= 0:
                pixels = NativeCmykPixels(pixels.inks, np.zeros_like(pixels.alpha), pixels.profile)
        finally:
            renderer.release()
        command = AddNativeAppearanceLayerCommand(f"{layer.name} (rasterized)", pixels)
    else:
        image = service._qimage_from_rgba(_rgb_foreground(canvas, layer))
        command = AddAppearanceLayerCommand(f"{layer.name} (rasterized)")
    location = canvas._find_parent(layer.id)
    if location is None:
        return None
    position = location[1] + 1
    service._begin("Rasterize Appearance")
    try:
        if not canvas.submit(command) or not command._layer_id:
            return None
        identifier = str(command._layer_id)
        if image is not None:
            canvas.submit(ReplaceLayerImageCommand(identifier, image))
        canvas.submit(ReparentLayerCommand(identifier, layer.parent_id, position))
        mode = getattr(layer.blend_mode, "value", layer.blend_mode)
        if mode not in {"normal", "pass_through"}:
            canvas.submit(SetLayerBlendModeCommand(identifier, mode))
        if layer.clipping:
            canvas.submit(ToggleClippingCommand(identifier, True))
        if not keep_source:
            canvas.submit(LayerVisibilityCommand(layer.id, False))
    finally:
        service._end()
    return identifier
