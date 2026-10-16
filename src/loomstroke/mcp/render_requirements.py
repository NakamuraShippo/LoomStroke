"""Renderer requirements for scenes whose editable text is not prepared yet."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from PySide6.QtGui import QGuiApplication

from loomstroke.core.snapshot_vector_rasterizer import SnapshotVectorRasterizer
from loomstroke.plugins.text_tool.common import is_text_vector_object
from loomstroke.ui.canvas.render_scene import (
    RenderLayerSnapshot,
    RenderSceneSnapshot,
    build_render_scene_snapshot,
)

from .types import McpStructuredError, ReasonCode


def build_canvas_scene_snapshot(canvas: Any) -> RenderSceneSnapshot:
    """Capture a canvas without entering GUI-only font paths in headless mode."""
    if isinstance(QGuiApplication.instance(), QGuiApplication):
        return build_render_scene_snapshot(canvas, rasterize_vector_layers=True)
    # False also avoids direct glyph capture, which runs before skip ids are
    # applied. Editable text remains inspectable without a GUI application.
    scene = build_render_scene_snapshot(canvas)
    scratch: SnapshotVectorRasterizer | None = None
    for layer in scene.flat_layers:
        if layer.layer_type not in {"vector", "text"}:
            continue
        if any(is_text_vector_object(obj) for obj in layer.vector_objects):
            layer.image = None
            continue
        if scratch is None:
            scratch = SnapshotVectorRasterizer(width=scene.width, height=scene.height)
        layer.image = scratch.rasterize(layer)
    return scene


def require_scene_text_renderer(scene: RenderSceneSnapshot) -> None:
    """Reject unprepared text without invoking Qt's GUI-only font database."""
    if not isinstance(QGuiApplication.instance(), QGuiApplication) and any(
        layer.image is None
        and not layer.vector_direct_ready
        and any(is_text_vector_object(obj) for obj in layer.vector_objects)
        for layer in _visible_layers(scene.layers)
    ):
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "Rendering editable text requires a Qt GUI application.",
            details={"requirement": "qt_gui_text_renderer"},
        )


def _visible_layers(layers: Sequence[RenderLayerSnapshot]) -> Iterator[RenderLayerSnapshot]:
    for layer in layers:
        if layer.visible and layer.opacity > 0:
            yield layer
            yield from _visible_layers(layer.children)
