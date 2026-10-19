"""Headless page-snapshot -> render-scene conversion (CT-10 X2).

Turns a persisted :class:`PageSnapshot` into the ``RenderSceneSnapshot``
the wgpu offscreen renderer consumes — without a live canvas.  Extracted
from the MCP service so the GUI multi-page export and the MCP batch
renderer convert pages the same way; the MCP service delegates here, so
its output is unchanged by the move.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from typing import Any

import numpy as np
from PySide6.QtGui import QColor, QGuiApplication, QImage

from loomstroke.core.appearance.live_styles import layer_styles
from loomstroke.core.canvas_models import (
    CanvasStateSnapshot,
    SelectionStateSnapshot,
    VectorLayerSnapshot,
    VectorStateSnapshot,
)
from loomstroke.core.layer_transform_metadata import read_layer_transform
from loomstroke.io.layer_io import DocumentMetadata, LayerSnapshot
from loomstroke.plugins.text_tool.common import is_text_vector_object

from .projection_state import compute_active_flat_idx
from .render_scene import RenderLayerSnapshot, RenderSceneSnapshot

__all__ = [
    "array_to_qimage",
    "flatten_render_layers",
    "page_background",
    "page_dimensions",
    "render_layer_from_snapshot",
    "scene_from_page",
]


def array_to_qimage(data: np.ndarray) -> QImage:
    arr = np.asarray(data, dtype=np.uint8)
    if arr.ndim != 3:
        arr = np.zeros((1, 1, 4), dtype=np.uint8)
    if arr.shape[2] == 3:
        alpha = np.full((arr.shape[0], arr.shape[1], 1), 255, dtype=np.uint8)
        arr = np.concatenate([arr, alpha], axis=2)
    if arr.shape[2] != 4:
        arr = np.zeros((arr.shape[0], arr.shape[1], 4), dtype=np.uint8)
    arr = np.ascontiguousarray(arr)
    height, width = arr.shape[:2]
    image = QImage(arr.data, width, height, arr.strides[0], QImage.Format.Format_RGBA8888)
    return image.copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def render_layer_from_snapshot(
    snapshot: LayerSnapshot,
    *,
    parent_id: str | None = None,
    rasterize_vector: Callable[[LayerSnapshot], QImage | None] | None = None,
) -> RenderLayerSnapshot:
    image = array_to_qimage(snapshot.image)
    if snapshot.layer_type in {"vector", "text"} and rasterize_vector is not None:
        image = rasterize_vector(snapshot)
    layer = RenderLayerSnapshot(
        id=snapshot.id,
        name=snapshot.name,
        visible=snapshot.visible,
        opacity=float(snapshot.opacity),
        blend_mode=snapshot.blend_mode,
        texture_version=0,
        image=image,
        style_stack=layer_styles(snapshot),
        native_cmyk=snapshot.native_cmyk,
        offset_x=int(snapshot.position[0]),
        offset_y=int(snapshot.position[1]),
        transform=read_layer_transform(snapshot.metadata, snapshot.position),
        locked=snapshot.locked,
        alpha_lock=snapshot.alpha_lock,
        layer_type=snapshot.layer_type,
        vector_kind=snapshot.vector_kind,
        vector_objects=list(snapshot.vector_objects),
        parent_id=parent_id,
        clipping=snapshot.clipping,
        layer_mask=(
            array_to_qimage(snapshot.layer_mask_image)
            if snapshot.layer_mask_image is not None
            else None
        ),
        mask_enabled=snapshot.mask_enabled,
        mask_nearest=bool(
            (snapshot.metadata or {})
            .get("loomstroke.comictools.frame_part", {})
            .get("nearest_mask", False)
        ),
        mask_region_eligible=bool(
            {"loomstroke.comictools.frame_part", "loomstroke.comictools.shared_content"}
            & (snapshot.metadata or {}).keys()
        ),
        effect_type=snapshot.filter_type,
        effect_params=dict(snapshot.filter_params),
        effect_mask=(
            array_to_qimage(snapshot.filter_mask_image)
            if snapshot.filter_mask_image is not None
            else None
        ),
        ai_effect=snapshot.ai_effect,
        origin=snapshot.origin,
    )
    layer.children = [
        render_layer_from_snapshot(child, parent_id=layer.id, rasterize_vector=rasterize_vector)
        for child in snapshot.children
    ]
    return layer


def flatten_render_layers(
    layers: Sequence[RenderLayerSnapshot],
    parent_id: str | None = None,
) -> tuple[list[RenderLayerSnapshot], list[str | None]]:
    flat: list[RenderLayerSnapshot] = []
    parents: list[str | None] = []
    for layer in layers:
        flat.append(layer)
        parents.append(parent_id)
        if layer.children:
            child_flat, child_parents = flatten_render_layers(layer.children, layer.id)
            flat.extend(child_flat)
            parents.extend(child_parents)
    return flat, parents


def page_dimensions(metadata: DocumentMetadata, page: Any) -> dict[str, int]:
    canvas = getattr(page, "canvas", None)
    if canvas is not None:
        return {"width": int(canvas.width), "height": int(canvas.height)}
    return {"width": int(metadata.width), "height": int(metadata.height)}


def page_background(metadata: DocumentMetadata, page: Any) -> tuple[int, int, int, int]:
    canvas = getattr(page, "canvas", None)
    if canvas is not None:
        r, g, b, a = canvas.background_color
        return (int(r), int(g), int(b), int(a))
    r, g, b, a = metadata.background_color
    return (int(r), int(g), int(b), int(a))


def scene_from_page(metadata: DocumentMetadata, page: Any) -> RenderSceneSnapshot:
    from loomstroke.core.document_snapshot_builder import (  # noqa: PLC0415
        DocumentSnapshotBuilder,
    )
    from loomstroke.core.snapshot_vector_rasterizer import (  # noqa: PLC0415
        SnapshotVectorRasterizer,
    )

    dimensions = page_dimensions(metadata, page)
    page_canvas = getattr(page, "canvas", None)
    page_dpi = page_canvas.dpi[0] if page_canvas is not None else metadata.dpi[0]
    canvas = CanvasStateSnapshot(
        width=dimensions["width"],
        height=dimensions["height"],
        dpi=int(page_dpi),
        background=QColor(*page_background(metadata, page)),
        active_layer_id=page.layers[-1].id if page.layers else None,
        active_index=max(0, len(page.layers) - 1),
        layer_cache_version=0,
    )
    scratch: SnapshotVectorRasterizer | None = None

    def rasterize_vector(snapshot: LayerSnapshot) -> QImage | None:
        nonlocal scratch
        if not isinstance(QGuiApplication.instance(), QGuiApplication) and any(
            is_text_vector_object(obj) for obj in snapshot.vector_objects
        ):
            # Inspection still exposes the objects without entering Qt's font
            # database, which aborts the process without a GUI application.
            # Export rejects this unprepared text layer at its service boundary.
            return None
        if scratch is None:
            # No EventBus, selection or live preview: export committed objects
            # with the normal core rasterizer, without changing the saved page.
            scratch = SnapshotVectorRasterizer(width=canvas.width, height=canvas.height)
        source = DocumentSnapshotBuilder.snapshot_to_simple_layer(deepcopy(snapshot))
        return scratch.rasterize(source)

    layers = tuple(
        render_layer_from_snapshot(layer, rasterize_vector=rasterize_vector)
        for layer in page.layers
    )
    flat_layers, parent_ids = flatten_render_layers(layers)
    active_layer_id = flat_layers[-1].id if flat_layers else None
    return RenderSceneSnapshot(
        canvas_state=canvas,
        layers=layers,
        flat_layers=tuple(flat_layers),
        parent_ids=tuple(parent_ids),
        active_layer_id=active_layer_id,
        active_flat_index=compute_active_flat_idx(flat_layers, active_layer_id),
        selection=SelectionStateSnapshot(selection_id="headless"),
        vector=VectorStateSnapshot(
            layers=tuple(
                VectorLayerSnapshot(
                    layer_id=layer.id,
                    vector_kind=layer.vector_kind,
                    vector_objects=tuple(layer.vector_objects),
                )
                for layer in flat_layers
                if layer.layer_type == "vector"
            )
        ),
    )
