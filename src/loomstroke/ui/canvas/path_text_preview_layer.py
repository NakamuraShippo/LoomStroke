"""Place a draft path text in the render snapshot without adding a document layer."""

from loomstroke.core.path_text.metadata import DRAFT_PREVIEW_LAYER_ID, path_settings


def append_path_text_preview(canvas, layers, rasterize_vector, scale):
    preview = getattr(canvas, "_vector_preview", None)
    if getattr(canvas, "_vector_preview_layer_id", None) != DRAFT_PREVIEW_LAYER_ID:
        return layers
    payload = (getattr(preview, "meta", None) or {}).get("text_tool")
    if path_settings(payload) is None:
        return layers
    from .render_scene import RenderLayerSnapshot  # noqa: PLC0415 - snapshot boundary

    draft = RenderLayerSnapshot(
        id=DRAFT_PREVIEW_LAYER_ID,
        name="",
        visible=True,
        opacity=1.0,
        blend_mode="normal",
        texture_version=id(preview),
        layer_type="text",
        vector_kind="text",
        vector_objects=[preview],
    )
    snapshot = RenderLayerSnapshot.from_layer(
        draft,
        rasterize_vector=rasterize_vector,
        copy_rasterized_vector=False,
        vector_raster_scale=scale,
    )
    return (*layers, snapshot)
