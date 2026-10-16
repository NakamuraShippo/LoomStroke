"""Release document-owned canvas state while keeping the widget reusable."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter

if TYPE_CHECKING:
    from .canvas_widget import CanvasWidget


def close_canvas_document_resources(widget: CanvasWidget) -> None:
    """Drop old document images, overlays and input without shutting down Qt."""
    widget._update_timer.stop()
    widget._marching_ants_timer.stop()
    widget._three_d_interaction.cancel()
    widget.input_handler.reset()
    widget._release_wgpu_bitmap_presenter()
    widget._overlay_manager.cleanup()
    widget._projection_tracker.reset()
    widget._projection_valid = False
    widget._below_projection_valid = False
    widget._layer_tex_versions.clear()
    widget._tool_preview_image = None
    widget._tool_preview_comp_mode = QPainter.CompositionMode.CompositionMode_SourceOver
    widget._tool_preview_opacity = 1.0
    widget._tool_overlay_fn = None
    widget._canvas_agent_overlay_fn = None
    widget._current_stroke.clear()
    widget._pending_stroke_points.clear()
    widget._is_drawing = False
    widget._pending_canvas_update = False
    widget._stroke_move_emitted = False
    widget._dirty_region = QRectF()
    widget._ants_path_cache = None
    widget._ants_path_mask_key = None
    widget._marching_ants_offset = 0.0
    widget._overlay_buffer = None
    widget._temp_buffer = None
    widget._buffers_initialized = False
