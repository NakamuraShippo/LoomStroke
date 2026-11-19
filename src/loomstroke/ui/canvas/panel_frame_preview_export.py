"""Restore committed layers before presenter export readback."""

from dataclasses import replace

from .projection_state import compute_active_flat_idx
from .render_scene import _flatten_layers_with_parents


def _committed_paint_layers(layers, layer):
    """Preserve the document version of the painted layer in the presenter's export inputs."""

    def restore(item):
        if item.id == layer.id:
            return replace(
                item,
                image=layer.image,
                texture_version=layer.texture_version,
                paint_upload=None,
                paint_upload_key=None,
            )
        if item.children:
            return replace(item, children=[restore(child) for child in item.children])
        return item

    return tuple(restore(item) for item in layers)


def viewport_preview_layers(
    canvas, layers, scale, *, paint_enabled, frame_enabled, selection_enabled
):
    paint = getattr(canvas, "_paint_tool_session", None) if paint_enabled else None
    source = None
    if paint is not None and paint.preview.image is not None:
        source = _committed_paint_layers(layers, paint.layer)
    liquify = getattr(canvas, "_liquify_layer_preview", None)
    if liquify is not None and liquify.current.image is getattr(
        canvas, "_selection_preview_image", None
    ):
        committed = liquify.original.apply_to_layers(source if source is not None else layers)
        if paint_enabled or selection_enabled:
            layers = liquify.current.apply_to_layers(layers)
            source = committed
        else:
            layers = committed
    frame = getattr(canvas, "_panel_frame_render_preview", None) if frame_enabled else None
    if frame is not None:
        if source is None:
            source = layers
        layers = frame.apply_to_layers(layers, scale=scale)
    return layers, source


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
