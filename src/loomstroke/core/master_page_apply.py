"""Materializing master instances onto pages (CT-07 T3a, spec 07 §5.3/§9).

Builders and lookups only — the page mutations live in
``PageServiceImpl`` and the atomicity in ``ApplyMasterInstancesCommand``.

- An instance is a **locked group at the top of the layer stack** whose
  layer metadata carries the :class:`MasterInstanceRef` marker.  Old
  readers see a normal group; nothing about the page schema changes.
- ``unlink`` converts the current look into ordinary layers: the marker
  goes away and the lock opens, the content stays (spec 07 §6.3).
- Lookup is by the metadata marker only, never by layer name (spec §9).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

import numpy as np

from ..io.layer_io import LayerSnapshot, deserialize_vector_objects, serialize_vector_objects
from .master_page_model import (
    MasterDefinition,
    MasterInstanceRef,
    MasterLayerDefinition,
    master_instance_marker,
    read_master_instance,
)

logger = logging.getLogger(__name__)

__all__ = [
    "build_master_instance_layer",
    "capture_master_layers_from_page",
    "find_master_instance",
    "unlinked_instance_layer",
]


def capture_master_layers_from_page(page: Any) -> tuple[MasterLayerDefinition, ...]:
    """Capture a page's top-level vector layers as master content (T3b).

    The capture-based editing model (2026-08-03, user approved): vector
    layers only, instance groups themselves excluded — capturing a page
    that already carries an instance must not nest the master into
    itself.
    """
    captured: list[MasterLayerDefinition] = []
    for layer in getattr(page, "layers", []) or []:
        if read_master_instance(layer.metadata) is not None:
            continue
        if getattr(layer, "layer_type", "raster") != "vector":
            continue
        vector_objects = list(getattr(layer, "vector_objects", []) or [])
        if not vector_objects:
            continue
        captured.append(
            MasterLayerDefinition(
                name=layer.name,
                vector_objects=tuple(serialize_vector_objects(vector_objects)),
                opacity=float(layer.opacity),
                blend_mode=str(layer.blend_mode),
                visible=bool(layer.visible),
            )
        )
    return tuple(captured)


def build_master_instance_layer(
    master: MasterDefinition,
    *,
    slot_id: str,
    new_layer_id: Callable[[], str],
    canvas_width: int,
    canvas_height: int,
) -> LayerSnapshot:
    """The materialized instance group for one page.

    Vector children are **rasterized into their layer image** at build
    time: the compositor draws layer rasters, so a blank-image vector
    layer would render invisible on non-active pages (found by the
    2026-08-03 headless verification).  The vector objects ride along
    too, so unlink still yields editable vectors.
    """
    children = []
    for layer in master.layers:
        vector_objects = deserialize_vector_objects(list(layer.vector_objects))
        children.append(
            LayerSnapshot(
                id=new_layer_id(),
                name=layer.name,
                image=_rasterized_vector_image(vector_objects, canvas_width, canvas_height),
                visible=layer.visible,
                opacity=float(layer.opacity),
                blend_mode=layer.blend_mode,
                locked=True,
                position=(0, 0),
                metadata=None,
                layer_type="vector" if vector_objects else "raster",
                vector_objects=vector_objects,
            )
        )
    return LayerSnapshot(
        id=new_layer_id(),
        name=master.name,
        image=_blank(1, 1),
        visible=True,
        opacity=1.0,
        blend_mode="normal",
        locked=True,
        position=(0, 0),
        metadata=master_instance_marker(
            MasterInstanceRef(master_id=master.id, revision=master.revision, slot_id=slot_id)
        ),
        layer_type="group",
        children=children,
    )


def find_master_instance(
    layers: Sequence[LayerSnapshot], master_id: str
) -> tuple[int, LayerSnapshot, MasterInstanceRef] | None:
    """The top-level instance of ``master_id`` on a page, if any."""
    for index, layer in enumerate(layers):
        ref = read_master_instance(layer.metadata)
        if ref is not None and ref.master_id == master_id:
            return index, layer, ref
    return None


def unlinked_instance_layer(layer: LayerSnapshot) -> LayerSnapshot:
    """The same content as ordinary, editable layers (marker gone)."""
    metadata = dict(layer.metadata) if isinstance(layer.metadata, dict) else None
    if metadata is not None:
        plugins = metadata.get("plugins")
        if isinstance(plugins, dict):
            plugins = dict(plugins)
            plugins.pop("loomstroke.comictools.master_instance", None)
            if plugins:
                metadata["plugins"] = plugins
            else:
                metadata.pop("plugins", None)
        if not metadata:
            metadata = None
    unlocked_children = [replace(child, locked=False) for child in layer.children]
    return replace(
        layer,
        locked=False,
        metadata=metadata,
        children=unlocked_children,
    )


def _blank(width: int, height: int) -> np.ndarray:
    return np.zeros((max(1, height), max(1, width), 4), dtype=np.uint8)


def _rasterized_vector_image(vector_objects: list[Any], width: int, height: int) -> np.ndarray:
    """Rasterize vector objects to a canvas-sized RGBA array.

    Uses the same ``VectorTextRenderService`` path the canvas uses for
    vector layers, over a scratch canvas of the target page's size.
    Falls back to a blank image (with a warning) if rasterization is
    unavailable — the vectors themselves are still stored, so unlink and
    later re-rasterization lose nothing.
    """
    if not vector_objects:
        return _blank(width, height)
    try:
        from .canvas_layer_model import _Layer  # noqa: PLC0415
        from .canvas_service_simple import SimpleCanvasService  # noqa: PLC0415
        from .document_snapshot_builder import DocumentSnapshotBuilder  # noqa: PLC0415

        scratch = SimpleCanvasService(width=max(1, width), height=max(1, height))
        layer = _Layer(
            id="master-rasterize",
            name="master-rasterize",
            layer_type="vector",
            vector_objects=list(vector_objects),
        )
        qimage = scratch._rasterize_vector_layer(layer)  # noqa: SLF001
        return DocumentSnapshotBuilder.qimage_to_array(qimage)
    except Exception:
        logger.warning("master vector rasterization failed; storing blank", exc_info=True)
        return _blank(width, height)
