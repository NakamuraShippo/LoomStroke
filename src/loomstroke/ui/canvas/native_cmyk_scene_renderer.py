"""Headless native ink output from the same tiled GPU compositor as the canvas."""

from dataclasses import replace

import numpy as np
from PySide6.QtCore import QRect

from loomstroke.core.native_cmyk.pixels import NativeCmykPixels

from .page_scene_snapshot import flatten_render_layers
from .render_scene import RenderLayerSnapshot, build_render_scene_snapshot
from .wgpu_backend.native_cmyk_compositor import NativeCmykCompositor


class NativeCmykSceneRenderer:
    @classmethod
    def from_canvas(cls, canvas, layers):
        scene = build_render_scene_snapshot(canvas)
        roots = tuple(RenderLayerSnapshot.from_layer(layer) for layer in layers)
        flat, parents = flatten_render_layers(roots)
        scene = replace(
            scene,
            layers=roots,
            flat_layers=tuple(flat),
            parent_ids=tuple(parents),
            channel_view=None,
        )
        renderer = cls()
        try:
            return renderer.render(scene)
        finally:
            renderer.release()

    def __init__(self, device_manager=None):
        self.compositor = NativeCmykCompositor(device_manager)

    def render(self, scene):
        source = next(
            (layer.native_cmyk for layer in scene.flat_layers if layer.native_cmyk is not None),
            None,
        )
        if source is None:
            raise ValueError("No native CMYK layers")
        shape = (scene.height, scene.width)
        if scene.width * scene.height * 5 * source.inks.dtype.itemsize > 256 * 1024 * 1024:
            raise ValueError("Native composite exceeds the pixel memory budget")
        inks = np.empty((*shape, 4), source.inks.dtype)
        alpha = np.empty(shape, source.alpha.dtype)
        size = self.compositor.TILE
        for y in range(0, scene.height, size):
            for x in range(0, scene.width, size):
                rect = QRect(x, y, min(size, scene.width - x), min(size, scene.height - y))
                tile = self.compositor.tile(scene, rect)
                region = np.s_[y : y + rect.height(), x : x + rect.width()]
                inks[region], alpha[region] = tile.inks, tile.alpha
        return NativeCmykPixels(inks, alpha, source.profile)

    def release(self):
        self.compositor.release()
