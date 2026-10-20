"""Detached raster candidate and source guard for confirmed vector/text filters."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from .comic_panels.membership import PART_KEY
from .command_types import LayerTransform
from .filter_pen.snapshot import FilterPenSnapshot
from .raster_edit_geometry import RasterEditGeometry, clip_signature, raster_ancestors


def require_rasterizable_filter_target(canvas: Any) -> Any:
    layer = canvas._get_active_layer()
    if layer is None or layer.layer_type not in {"vector", "text"}:
        raise ValueError("Select a vector or text layer to rasterize.")
    if any(node.locked or node.mask_edit_mode for node in raster_ancestors(canvas, layer)):
        raise ValueError("Unlock the layer and its parent groups before applying a filter.")
    if canvas._in_transaction or getattr(canvas, "_vector_preview", None) is not None:
        raise ValueError("Finish the current stroke or text edit first.")
    if getattr(layer, "_preview_transform", None) is not None:
        raise ValueError("Finish the current stroke or text edit first.")
    role = (layer.metadata or {}).get(PART_KEY, {}).get("role")
    if role in {"edge", "restore", "carrier"}:
        raise ValueError("Use this managed layer's own controls to rasterize it.")
    return layer


class _RasterCanvasView:
    def __init__(self, canvas: Any, raster: Any):
        self.canvas, self.raster = canvas, raster

    def _get_active_layer(self) -> Any:
        return self.raster

    def __getattr__(self, name: str) -> Any:
        return getattr(self.canvas, name)


class RasterizedFilterTarget:
    """Keep original object data authoritative until the final filter commit."""

    FIELDS = (
        "layer_type",
        "vector_kind",
        "vector_objects",
        "image",
        "native_cmyk",
        "offset_x",
        "offset_y",
        "transform",
    )

    def __init__(self, canvas: Any, raster: Any) -> None:
        self.source = require_rasterizable_filter_target(canvas)
        self.raster = raster
        self.version = self.source.texture_version
        self.before = {key: getattr(self.source, key) for key in self.FIELDS}
        self.before["vector_objects"] = deepcopy(self.source.vector_objects)
        self._objects = self.source.vector_objects
        self._metadata = deepcopy(self.source.metadata)
        self._signature = clip_signature(canvas, self.source)
        self._native_format = self._document_native_format(canvas)
        self.selection = deepcopy(getattr(canvas, "_vector_selection", None))
        self.view = _RasterCanvasView(canvas, raster)

    def snapshot(self) -> FilterPenSnapshot:
        # Capture standard selection/channel/mask semantics before restoring the
        # confirmed placement. The raster keeps that placement after conversion.
        neutral = replace(self.raster, transform=LayerTransform.identity())
        snapshot = FilterPenSnapshot.capture(_RasterCanvasView(self.view.canvas, neutral))
        placed = self.raster.transform != LayerTransform.identity()
        return replace(
            snapshot,
            layer=self.raster,
            transform=self.raster.transform,
            geometry=RasterEditGeometry.capture(self.raster) if placed else None,
        )

    def valid(self, canvas: Any) -> bool:
        layer = canvas._get_active_layer()
        try:
            signature = clip_signature(canvas, layer) if layer is self.source else None
            editable = not any(
                node.locked or node.mask_edit_mode for node in raster_ancestors(canvas, layer)
            )
        except ValueError:
            return False
        return bool(
            layer is self.source
            and layer.layer_type == self.before["layer_type"]
            and layer.texture_version == self.version
            and layer.vector_objects is self._objects
            and layer.metadata == self._metadata
            and self._document_native_format(canvas) == self._native_format
            and (layer.offset_x, layer.offset_y, layer.transform)
            == (self.before["offset_x"], self.before["offset_y"], self.before["transform"])
            and editable
            and getattr(layer, "_preview_transform", None) is None
            and signature == self._signature
        )

    @staticmethod
    def _document_native_format(canvas: Any) -> Any:
        native = next(
            (node.native_cmyk for node in canvas._flatten_layers() if node.native_cmyk is not None),
            None,
        )
        return (native.profile, native.inks.dtype.str) if native is not None else None
