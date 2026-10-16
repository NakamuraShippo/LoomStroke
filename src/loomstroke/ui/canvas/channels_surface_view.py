"""Display-only channel inspection through the existing wgpu surface pass."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtGui import QColor

from loomstroke.core.canvas_models import VectorStateSnapshot
from loomstroke.core.native_cmyk.channel_image import NativeCmykChannelImage

from .native_cmyk_channel_view_cache import NativeCmykChannelViewCache
from .render_scene import RenderSceneSnapshot


class ChannelsSurfaceView:
    @classmethod
    def prepare(cls, presenter, scene):
        cache = getattr(presenter, "_native_channel_view_cache", None)
        if cache is None:
            cache = NativeCmykChannelViewCache()
            presenter._native_channel_view_cache = cache
        return cls.scene(scene, cache=cache)

    @staticmethod
    def scene(scene: RenderSceneSnapshot, *, cache=None) -> tuple[RenderSceneSnapshot, int]:
        view = scene.channel_view
        if view is None:
            return scene, -1
        layer_id, channel = view
        index = {"r": 0, "g": 1, "b": 2, "transparency": 3}.get(channel, -1)
        layer = next((layer for layer in scene.flat_layers if layer.id == layer_id), None)
        if (
            layer is not None
            and layer.native_cmyk is not None
            and channel in ("c", "m", "y", "k", "transparency")
        ):
            image = (
                cache.image(layer.native_cmyk, channel)
                if cache is not None
                else NativeCmykChannelImage.image(layer.native_cmyk, channel)
            )
            layer = replace(
                layer, image=image, preview_dirty_rect=cache.dirty if cache is not None else None
            )
            index = 0
        if index < 0 or layer is None or layer.image is None:
            return scene, -1
        display_layer = replace(
            layer,
            layer_type="raster",
            visible=True,
            opacity=1.0,
            blend_mode="normal",
            clipping=False,
            layer_mask=None,
            parent_id=None,
            children=[],
        )
        return (
            replace(
                scene,
                canvas_state=replace(scene.canvas_state, background=QColor(0, 0, 0, 0)),
                layers=(display_layer,),
                flat_layers=(display_layer,),
                parent_ids=(None,),
                active_flat_index=0,
                vector=VectorStateSnapshot(),
                selection_preview_image=None,
                selection_preview_bounds=None,
            ),
            index,
        )

    @staticmethod
    def shader(source: str, index: int) -> str:
        if index not in (0, 1, 2, 3):
            return source
        value = (
            "src.a"
            if index == 3
            else f"select(0.0, src[{index}] / max(src.a, 0.00001), src.a > 0.0)"
        )
        return source.replace(
            "let rgb_srgb = proof_rgb(src.rgb + checker * (1.0 - alpha));",
            f"let rgb_srgb = vec3<f32>(clamp({value}, 0.0, 1.0));",
        )
