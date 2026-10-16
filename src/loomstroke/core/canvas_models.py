"""Lightweight canvas state and snapshot models.

These dataclasses intentionally carry identity and renderer/save inputs without
owning live canvas mutation state.  They are the first boundary between the
current ``SimpleCanvasService`` facade and future renderer backends.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QPointF, QRect
from PySide6.QtGui import QColor, QImage

from .mask_shape_queries import MaskShapeQueries


def _copy_rect(rect: QRect | None) -> QRect | None:
    return QRect(rect) if rect is not None else None


def _copy_image(image: QImage | None) -> QImage | None:
    return image.copy() if image is not None and not image.isNull() else None


@dataclass(frozen=True, slots=True)
class LayerIdentity:
    """Stable layer identity independent from the mutable layer object."""

    id: str
    name: str
    parent_id: str | None
    index: int
    layer_type: str = "raster"
    vector_kind: str = "draw"


@dataclass(frozen=True, slots=True)
class LayerTreeSnapshot:
    """Identity-only snapshot of the layer tree."""

    layers: tuple[LayerIdentity, ...]
    root_layer_ids: tuple[str, ...]
    active_layer_id: str | None
    layer_cache_version: int = 0

    @property
    def layer_ids(self) -> tuple[str, ...]:
        return tuple(layer.id for layer in self.layers)

    def contains(self, layer_id: str) -> bool:
        return any(layer.id == layer_id for layer in self.layers)


@dataclass(frozen=True, slots=True)
class CanvasStateSnapshot:
    """Renderer-neutral canvas scalar state."""

    width: int
    height: int
    dpi: int
    background: QColor
    active_layer_id: str | None
    active_index: int
    layer_cache_version: int = 0

    @property
    def background_rgba(self) -> tuple[int, int, int, int]:
        return (
            self.background.red(),
            self.background.green(),
            self.background.blue(),
            self.background.alpha(),
        )


@dataclass(frozen=True, slots=True)
class SelectionStateSnapshot:
    """Deep-copied selection state safe for renderer/save consumers."""

    selection_id: str
    mask: QImage | None = None
    bounds: QRect | None = None
    preview_image: QImage | None = None
    preview_bounds: tuple[int, int, int, int] | None = None
    mask_full: Any | None = None

    @property
    def has_selection(self) -> bool:
        return self.mask is not None and self.bounds is not None and not self.bounds.isEmpty()

    @classmethod
    def from_parts(
        cls,
        *,
        selection_id: str,
        mask: QImage | None,
        bounds: QRect | None,
        preview_image: QImage | None = None,
        preview_bounds: tuple[int, int, int, int] | None = None,
        mask_full: Any | None = None,
    ) -> SelectionStateSnapshot:
        mask_full_copy = (
            mask_full.copy() if mask_full is not None and hasattr(mask_full, "copy") else mask_full
        )
        return cls(
            selection_id=selection_id,
            mask=_copy_image(mask),
            bounds=_copy_rect(bounds),
            preview_image=_copy_image(preview_image),
            preview_bounds=preview_bounds,
            mask_full=mask_full_copy,
        )


@dataclass(frozen=True, slots=True)
class VectorLayerSnapshot:
    """Identity and object list for a vector layer."""

    layer_id: str
    vector_kind: str = "draw"
    vector_objects: tuple[Any, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class VectorStateSnapshot:
    """Vector edit state separated from renderer internals."""

    layers: tuple[VectorLayerSnapshot, ...] = field(default_factory=tuple)
    preview_layer_id: str | None = None
    preview_object: Any | None = None
    selection: Any | None = None

    def layer(self, layer_id: str) -> VectorLayerSnapshot | None:
        for layer in self.layers:
            if layer.layer_id == layer_id:
                return layer
        return None


@dataclass(frozen=True, slots=True)
class LayerTreeMutationResult:
    """Result envelope for future layer tree mutation adapters."""

    changed: bool
    active_layer_id: str | None = None
    affected_layer_ids: tuple[str, ...] = field(default_factory=tuple)
    reason: str | None = None


class _MaskShapeContour:
    """Duck-typed replacement for QPainterPath used by _shape_segments_at_y.

    Wraps a grayscale selection mask so that ``contains(QPointF)`` tests the
    mask pixel value directly — more accurate and faster than converting the
    mask to a vector QPainterPath.
    """

    __slots__ = ("_mask", "_ox", "_oy", "_queries", "_query_key")

    def __init__(self, mask: QImage, offset_x: int = 0, offset_y: int = 0) -> None:
        self._mask = mask
        self._ox = offset_x
        self._oy = offset_y
        self._queries = None
        self._query_key = None

    def _pixel_queries(self) -> MaskShapeQueries:
        if self._queries is None or self._query_key != self._mask.cacheKey():
            self._queries = MaskShapeQueries(self._mask, self._ox, self._oy)
            self._query_key = self._mask.cacheKey()
        return self._queries

    def segments_at_y(self, x0, x1, y):
        return self._pixel_queries().segments(x0, x1, y)

    def segments_at_x(self, y0, y1, x):
        return self._pixel_queries().segments(y0, y1, x, vertical=True)

    def contains_rect(self, rect):
        return self._pixel_queries().contains_rect(rect)

    def contains(self, point: QPointF) -> bool:  # noqa: D401
        mx = int(point.x()) - self._ox
        my = int(point.y()) - self._oy
        if 0 <= mx < self._mask.width() and 0 <= my < self._mask.height():
            # QImage.pixel() returns QRgb (uint32).  For Format_Alpha8 and
            # Format_ARGB32 the alpha lives in the top byte, so shift >> 24.
            return ((self._mask.pixel(mx, my) >> 24) & 0xFF) > 127
        return False


def _is_text_vector_object(vector_object: Any) -> bool:
    meta = getattr(vector_object, "meta", None)
    if not isinstance(meta, Mapping):
        return False
    return isinstance(meta.get("text_tool"), Mapping)
