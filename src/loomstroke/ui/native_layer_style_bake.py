"""Native appearance baking uses the same GPU ink styles as canvas and export."""

from collections.abc import Callable
from copy import copy
from typing import Any

from loomstroke.core.appearance.add_native_appearance_layer_command import (
    AddNativeAppearanceLayerCommand,
)
from loomstroke.core.commands_layer_transform import LayerVisibilityCommand
from loomstroke.core.native_cmyk.pixels import NativeCmykPixels

from .canvas.native_cmyk_scene_renderer import NativeCmykSceneRenderer


def bake_native_styles(service: Any, layer: Any, *, keep_source: bool) -> str | None:
    canvas = service.canvas
    source = copy(layer)
    source.opacity, source.blend_mode = 1.0, "normal"
    source.layer_mask = None
    source.children = list(layer.children) if layer.layer_type == "group" else []
    source.offset_x = source.offset_y = 0
    source.transform = source._preview_transform = None
    source.visible, source.clipping = True, False
    layers = [source]
    if source.native_cmyk is None:
        reference = next((n for n in canvas._flatten_layers() if n.native_cmyk is not None), None)
        if reference is None:
            return None
        carrier = copy(reference)
        carrier.visible, carrier.children = False, []
        layers.insert(0, carrier)
    render_native: Callable[[Any, Any], NativeCmykPixels] = NativeCmykSceneRenderer.from_canvas
    pixels = render_native(canvas, layers)
    command = AddNativeAppearanceLayerCommand(f"{layer.name} (rasterized)", pixels)
    service._begin("Rasterize Appearance")
    try:
        if not canvas.submit(command):
            return None
        if not keep_source:
            canvas.submit(LayerVisibilityCommand(layer.id, False))
    finally:
        service._end()
    return str(command._layer_id)
