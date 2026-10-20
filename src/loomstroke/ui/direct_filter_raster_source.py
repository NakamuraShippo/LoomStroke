"""Prepare the raw vector/text raster using existing RGB/native renderers."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from typing import Any

from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.native_cmyk.buffer import NativeCmykBuffer
from loomstroke.core.native_cmyk.pixels import NativeCmykPixels
from loomstroke.core.rasterized_filter_target import (
    RasterizedFilterTarget,
    require_rasterizable_filter_target,
)
from loomstroke.core.snapshot_vector_rasterizer import SnapshotVectorRasterizer


def prepare_filter_raster(canvas: Any) -> RasterizedFilterTarget:
    layer = require_rasterizable_filter_target(canvas)
    source = replace(layer, vector_objects=deepcopy(layer.vector_objects))
    native = next((item for item in canvas._flatten_layers() if item.native_cmyk is not None), None)
    if native is None:
        image = SnapshotVectorRasterizer(width=canvas._width, height=canvas._height).rasterize(
            source
        )
        buffer = None
    else:
        from .canvas.native_cmyk_scene_renderer import NativeCmykSceneRenderer  # noqa: PLC0415

        # Bake raw object inks only; layer masks/styles/children/placement remain live.
        source = replace(
            source,
            opacity=1.0,
            blend_mode="normal",
            visible=True,
            clipping=False,
            children=[],
            layer_mask=None,
            offset_x=0,
            offset_y=0,
            transform=LayerTransform.identity(),
            metadata={},
        )
        reference = replace(native, visible=False, children=[])
        render_native: Callable[[Any, Any], NativeCmykPixels] = NativeCmykSceneRenderer.from_canvas
        pixels = render_native(canvas, [reference, source])
        buffer = NativeCmykBuffer(pixels)
        image = ChannelPixelPolicy.image(pixels.preview(), straight=True)
    if image is None or image.isNull():
        raise ValueError("The layer could not be rasterized. The original objects were preserved.")
    raster = replace(
        layer,
        layer_type="raster",
        vector_kind="draw",
        vector_objects=[],
        image=image,
        native_cmyk=buffer,
    )
    return RasterizedFilterTarget(canvas, raster)
