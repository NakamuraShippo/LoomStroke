"""Keep bitmap display responsive while its device prepares style programs."""

from typing import Any

from .render_scene import RenderSceneSnapshot, compute_render_scene_signature
from .wgpu_backend.style_pipeline_preparation import StylePipelinePreparation, without_styles


class BitmapStylePreparation:
    def __init__(self, compositor: Any, repaint: Any) -> None:
        self._preparation = StylePipelinePreparation(
            compositor.layer_style_processor.pipeline_store, repaint
        )
        self._last_base: tuple[Any, ...] | None = None
        self.full_readback = False

    def scene(self, scene: RenderSceneSnapshot, *, has_frame: bool) -> RenderSceneSnapshot | None:
        was_pending = self._preparation.pending
        ready = self._preparation.ready(scene)
        self.full_readback = was_pending or not ready
        base = without_styles(scene)
        signature = compute_render_scene_signature(base)
        if not ready and has_frame and signature == self._last_base:
            return None
        self._last_base = signature
        return scene if ready else base

    def release(self) -> None:
        self._preparation.close()
