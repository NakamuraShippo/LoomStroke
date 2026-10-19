"""Project a native selection inside its original compositing position."""

from dataclasses import replace
from typing import Any


def project_selection(scene: Any) -> Any:
    preview = scene.native_selection_preview
    if preview is None:
        return scene
    flat = []

    def project(layer: Any) -> Any:
        result = replace(layer, children=[])
        if layer.id == preview.layer_id:
            result.native_cmyk = preview.native
            result.transform = preview.transform
            result.offset_x = round(preview.transform.tx)
            result.offset_y = round(preview.transform.ty)
            result.texture_version += 1
            result._preview_transform = None
        flat.append(result)
        result.children = [project(child) for child in layer.children]
        return result

    roots = tuple(project(layer) for layer in scene.layers)
    return replace(
        scene,
        layers=roots,
        flat_layers=tuple(flat),
        native_selection_preview=None,
        selection_preview_image=None,
        selection_preview_bounds=None,
    )
