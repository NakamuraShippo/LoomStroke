"""Bind renderer-neutral 3D layer capture to the existing wgpu pipeline."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from PySide6.QtGui import QColor, QImage

from loomstroke.core.appearance import LayerStyleStack
from loomstroke.core.canvas_models import (
    CanvasStateSnapshot,
    SelectionStateSnapshot,
    VectorStateSnapshot,
)

from .canvas.render_scene import RenderLayerSnapshot, RenderSceneSnapshot
from .canvas.wgpu_backend import WgpuOffscreenRenderer


class LayerSnapshotRenderer:
    """Own a lazy offscreen renderer and restore older live bindings on close."""

    def __init__(self, canvas: Any) -> None:
        self._canvas = canvas
        self._previous = getattr(canvas, "_three_d_snapshot_renderer", None)
        self._renderer: WgpuOffscreenRenderer | None = None
        self._scene: RenderSceneSnapshot | None = None
        self._closed = False
        canvas._three_d_snapshot_renderer = self

    def __call__(self, layer: Any, width: int, height: int) -> QImage:
        if self._closed:
            raise RuntimeError("3D layer snapshot renderer is closed")
        rendered_layer = replace(
            RenderLayerSnapshot.from_layer(layer),
            visible=True,
            opacity=1.0,
            blend_mode="normal",
            clipping=False,
            children=[],
            parent_id=None,
            layer_mask=None,
            mask_enabled=False,
            style_stack=LayerStyleStack(),
            origin=None,
        )
        # Styles are baked exactly once by the existing input snapshot path.
        # No paper, selection, guide, ring, or other source layer is captured.
        scene = RenderSceneSnapshot(
            canvas_state=CanvasStateSnapshot(width, height, 96, QColor(0, 0, 0, 0), layer.id, 0),
            layers=(rendered_layer,),
            flat_layers=(rendered_layer,),
            parent_ids=(None,),
            active_layer_id=layer.id,
            active_flat_index=0,
            selection=SelectionStateSnapshot(""),
            vector=VectorStateSnapshot(),
        )
        if self._renderer is None:
            self._renderer = WgpuOffscreenRenderer()
        result = self._renderer.render_scene(scene)
        self._scene = scene
        rgba = result.rgba
        return QImage(
            rgba.data, width, height, rgba.strides[0], QImage.Format.Format_RGBA8888
        ).copy()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        canvas, self._canvas = self._canvas, None
        if canvas is not None and getattr(canvas, "_three_d_snapshot_renderer", None) is self:
            previous = self._previous
            while isinstance(previous, LayerSnapshotRenderer) and previous._closed:
                previous = previous._previous
            canvas._three_d_snapshot_renderer = previous
        if self._renderer is not None:
            self._renderer.release()
            self._renderer = None
        self._scene = None


def bind_layer_snapshot_renderer(widget: Any, host: Any) -> None:
    canvas = getattr(host, "canvas", None)
    existing = getattr(widget, "_layer_snapshot_renderer_binding", None)
    if existing is not None:
        if not existing._closed and existing._canvas is canvas:
            return
        existing.close()
    widget._layer_snapshot_renderer_binding = None
    if canvas is not None and callable(getattr(canvas, "_rasterize_3d_layer", None)):
        binding = LayerSnapshotRenderer(canvas)
        widget._layer_snapshot_renderer_binding = binding
        widget.destroyed.connect(binding.close)


def release_layer_snapshot_renderer(widget: Any) -> None:
    binding = getattr(widget, "_layer_snapshot_renderer_binding", None)
    if binding is not None:
        binding.close()
        widget._layer_snapshot_renderer_binding = None
