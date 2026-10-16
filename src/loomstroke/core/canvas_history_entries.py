"""Runtime history entry models for SimpleCanvasService.

These entries are in-memory undo/redo payloads only; ``.lstroke`` save
data is produced from layer snapshots, not from these classes.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.commands import BlendMode, Command


def _compress_qimage(img: QImage) -> tuple[bytes, int, int, QImage.Format]:
    """Store a QImage as zlib bytes, returning (data, w, h, format).

    Level 0 (store-only) is used instead of level 1 to avoid blocking the
    Qt main thread for hundreds of ms on large-canvas strokes.  The undo
    history is in-memory only, so compression ratio is not critical.
    """
    w, h, fmt = img.width(), img.height(), img.format()
    bits = img.constBits()
    raw = bytes(bits) if bits is not None else b""
    return (zlib.compress(raw, 0), w, h, fmt)


def _decompress_qimage(data: bytes, w: int, h: int, fmt: QImage.Format) -> QImage:
    """Reconstruct a QImage from compressed data."""
    raw = zlib.decompress(data)
    img = QImage(raw, w, h, fmt).copy()  # .copy() detaches from raw buffer
    return img


@dataclass(slots=True)
class _HistoryPatch:
    """Captured region for undo/redo operations (zlib-compressed)."""

    layer_id: str
    rect: QRect
    _before_data: bytes
    _after_data: bytes
    _img_w: int
    _img_h: int
    _img_fmt: QImage.Format
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_images(
        cls,
        layer_id: str,
        rect: QRect,
        before: QImage,
        after: QImage,
        metadata: dict[str, Any] | None = None,
    ) -> _HistoryPatch:
        b_data, w, h, fmt = _compress_qimage(before)
        a_data, _, _, _ = _compress_qimage(after)
        return cls(
            layer_id=layer_id,
            rect=rect,
            _before_data=b_data,
            _after_data=a_data,
            _img_w=w,
            _img_h=h,
            _img_fmt=fmt,
            metadata=metadata or {},
        )

    @property
    def before(self) -> QImage:
        return _decompress_qimage(self._before_data, self._img_w, self._img_h, self._img_fmt)

    @property
    def after(self) -> QImage:
        return _decompress_qimage(self._after_data, self._img_w, self._img_h, self._img_fmt)


@dataclass(slots=True)
class _HistoryCommand:
    """Command-based history entry (vector operations)."""

    command: Command
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _ResizeSnapshot:
    """Snapshot of canvas state before/after resize for undo/redo."""

    width: int
    height: int
    layer_images: list[tuple[str, bytes, int, int, int, int]]
    # (layer_id, compressed_data, w, h, offset_x, offset_y)
    layer_vectors: dict[str, list[Any]] = field(default_factory=dict)
    # layer_id -> snapshot list of vector_objects (frozen dataclasses → shallow list copy
    # is sufficient since transforms rebuild instances rather than mutating in place).
    # Runtime-only metadata for editable layer geometry (e.g. panel trees).
    layer_metadata: dict[str, Any] = field(default_factory=dict)
    native_layers: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _ResizeEntry:
    """History entry for canvas resize undo/redo."""

    before: _ResizeSnapshot
    after: _ResizeSnapshot
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _MergeEntry:
    """History entry for layer merge-down undo/redo."""

    source_id: str
    source_name: str
    source_index: int
    source_image_data: bytes
    source_img_w: int
    source_img_h: int
    source_img_fmt: QImage.Format
    source_offset_x: int
    source_offset_y: int
    source_blend_mode: BlendMode
    source_opacity: float
    source_visible: bool
    source_locked: bool
    target_id: str
    target_image_before: bytes
    target_img_w: int
    target_img_h: int
    target_img_fmt: QImage.Format
    active_index_before: int
    source_layer_type: str = "raster"
    source_vector_kind: str = "draw"
    source_clipping: bool = False
    source_alpha_lock: bool = False
    source_origin: Any = None
    source_scene_3d: Any = None
    source_camera_3d: Any = None
    source_lighting_3d: Any = None
    source_display_mode_3d: str = "solid"
    source_gen_3d: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _MergeVisibleEntry:
    """History entry for stamp-visible (merge visible) undo/redo.

    Stamp Visible keeps all original layers and adds a new merged
    result layer on top of the stack.  Undo simply removes the
    stamped layer; redo re-inserts it.
    """

    result_id: str
    result_name: str
    result_image_data: bytes
    result_img_w: int
    result_img_h: int
    result_img_fmt: QImage.Format
    result_index: int
    active_index_before: int
    active_layer_id_before: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _FlattenAllEntry:
    """History entry for Flatten All undo/redo.

    Flatten All keeps all original layers and adds a new flattened
    result layer on top of the stack.  Undo removes the flattened
    layer; redo re-inserts it.
    """

    result_id: str
    result_name: str
    result_image_data: bytes
    result_img_w: int
    result_img_h: int
    result_img_fmt: QImage.Format
    result_index: int
    active_index_before: int
    active_layer_id_before: str
    hidden_layer_ids: list[str] = field(default_factory=list)


HistoryEntry: TypeAlias = (
    _HistoryPatch
    | _HistoryCommand
    | _ResizeEntry
    | _MergeEntry
    | _MergeVisibleEntry
    | _FlattenAllEntry
)


@dataclass(slots=True)
class _HistoryGroup:
    """Grouped history entries that should undo as a single unit.

    ``sequence`` is the document-local history sequence (CT-05 H5a),
    assigned by the bookkeeping service when a non-empty group is pushed
    onto the undo stack.  ``0`` (the backward-compatible default) marks a
    legacy/unwired group.  Runtime-only; never persisted.
    """

    label: str
    entries: list[HistoryEntry] = field(default_factory=list)
    sequence: int = 0
