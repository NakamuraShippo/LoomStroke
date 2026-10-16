"""Restore committed frame layers before presenter export readback."""

from dataclasses import replace

from .projection_state import compute_active_flat_idx
from .render_scene import _flatten_layers_with_parents


def without_frame_preview(scene):
    if scene.frame_preview_source_layers is None:
        return scene
    layers = scene.frame_preview_source_layers
    flat, parents = _flatten_layers_with_parents(list(layers))
    return replace(
        scene,
        layers=layers,
        flat_layers=tuple(flat),
        parent_ids=tuple(parents),
        active_flat_index=compute_active_flat_idx(flat, scene.active_layer_id),
        frame_preview_source_layers=None,
    )
