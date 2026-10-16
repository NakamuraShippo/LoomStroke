"""Retained-memory estimation for the page book history (CT-05 H4d1).

These helpers approximate how many bytes a book history entry keeps
alive *outside* the document: off-document ``PageSnapshot`` payloads,
runtime thumbnail bytes, and detached ``PageHistoryBundle`` contents.
The result is a consistent trim heuristic, not a process-RSS truth.

Hard rules: never copy or decompress payloads (``_HistoryPatch.before``
/ ``after`` are never touched — only their compressed byte fields),
never serialize, never recurse into unknown object graphs, and count
every shared object once via an ``id()``-based ``seen`` set supplied by
the caller.
"""

from __future__ import annotations

import logging
from itertools import chain
from typing import Any

import numpy as np

from loomstroke.core.canvas_history_entries import (
    _FlattenAllEntry,
    _HistoryCommand,
    _HistoryPatch,
    _MergeEntry,
    _MergeVisibleEntry,
    _ResizeEntry,
)

logger = logging.getLogger(__name__)

# Flat cost charged for objects we do not introspect (opaque commands,
# in-memory 3D scenes, plugin payloads).  Deliberately small: the budget
# targets raster snapshots and compressed history bytes, not bookkeeping.
_OPAQUE_OBJECT_OVERHEAD = 64
_SCALAR_OVERHEAD = 32
_CONTAINER_OVERHEAD = 64
_ENTRY_OVERHEAD = 128
_MAX_DEPTH = 6


def _bytes_len(data: Any, seen: set[int]) -> int:
    if data is None:
        return 0
    key = id(data)
    if key in seen:
        return 0
    seen.add(key)
    try:
        return len(data)
    except TypeError:
        return _OPAQUE_OBJECT_OVERHEAD


def _estimate_leaf(obj: Any) -> int | None:
    if isinstance(obj, str):
        return _SCALAR_OVERHEAD + len(obj)
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return len(obj)
    if isinstance(obj, np.ndarray):
        return int(obj.nbytes)
    return None


def _estimate_generic(obj: Any, seen: set[int], depth: int) -> int:
    """Bounded, cycle-safe estimate for metadata-like payloads."""
    if obj is None or isinstance(obj, (bool, int, float)):
        return _SCALAR_OVERHEAD
    key = id(obj)
    if key in seen:
        return 0
    leaf = _estimate_leaf(obj)
    if leaf is not None:
        seen.add(key)
        return leaf
    if depth <= 0 or not isinstance(obj, (dict, list, tuple, set, frozenset)):
        return _OPAQUE_OBJECT_OVERHEAD
    seen.add(key)
    items = chain.from_iterable(obj.items()) if isinstance(obj, dict) else obj
    total = _CONTAINER_OVERHEAD
    for item in items:
        total += _estimate_generic(item, seen, depth - 1)
    return total


def _estimate_ndarray(array: Any, seen: set[int]) -> int:
    if array is None:
        return 0
    key = id(array)
    if key in seen:
        return 0
    seen.add(key)
    nbytes = getattr(array, "nbytes", None)
    if nbytes is not None:
        return int(nbytes)
    return _OPAQUE_OBJECT_OVERHEAD


def _estimate_layer_snapshot(layer: Any, seen: set[int]) -> int:
    key = id(layer)
    if key in seen:
        return 0
    seen.add(key)
    total = _ENTRY_OVERHEAD
    total += _estimate_ndarray(getattr(layer, "image", None), seen)
    native = getattr(layer, "native_cmyk", None)
    if native is not None:
        total += _estimate_ndarray(native.inks, seen) + _estimate_ndarray(native.alpha, seen)
        total += _bytes_len(native.profile, seen)
    total += _estimate_ndarray(getattr(layer, "layer_mask_image", None), seen)
    total += _estimate_ndarray(
        getattr(layer, "filter_mask_image", getattr(layer, "effect_mask_image", None)), seen
    )
    total += _estimate_generic(
        getattr(layer, "filter_params", getattr(layer, "effect_params", None)), seen, _MAX_DEPTH
    )
    for payload_name in (
        "metadata",
        "vector_objects",
        "ai_effect",
        "origin",
        "camera_3d",
        "lighting_3d",
        "gen_3d",
    ):
        total += _estimate_generic(getattr(layer, payload_name, None), seen, _MAX_DEPTH)
    if getattr(layer, "scene_3d", None) is not None:
        total += _OPAQUE_OBJECT_OVERHEAD
    for child in getattr(layer, "children", None) or ():
        total += _estimate_layer_snapshot(child, seen)
    return total


def estimate_layer_snapshot_bytes(layer: Any, *, seen: set[int]) -> int:
    """Public wrapper for single-layer payloads held by commands
    (CT-07 T3a master instances). Same estimator as page snapshots."""
    return _estimate_layer_snapshot(layer, seen)


def estimate_metadata_payload_bytes(payload: Any, *, seen: set[int]) -> int:
    """Approximate the retained bytes of an opaque metadata-like payload.

    Public wrapper around the existing bounded-depth, cycle-safe
    ``_estimate_generic`` estimator (CT-06 Phase B3-c2a) for command
    payloads that are plain dict/list/scalar trees (e.g.
    ``SetBookLayoutCommand`` snapshots) rather than ``PageSnapshot`` /
    ``_HistoryGroup`` / ``PageHistoryBundle`` objects. No new estimation
    logic: same depth bound, same ``seen`` dedup contract, no
    serialization or decompression.
    """
    return _estimate_generic(payload, seen, _MAX_DEPTH)


def estimate_page_snapshot_bytes(page: Any, *, seen: set[int]) -> int:
    """Approximate the retained bytes of an off-document ``PageSnapshot``."""
    if page is None:
        return 0
    key = id(page)
    if key in seen:
        return 0
    seen.add(key)
    total = _ENTRY_OVERHEAD
    for layer in getattr(page, "layers", None) or ():
        total += _estimate_layer_snapshot(layer, seen)
    total += _estimate_generic(getattr(page, "metadata", None), seen, _MAX_DEPTH)
    total += _estimate_generic(getattr(page, "view_state", None), seen, _MAX_DEPTH)
    return total


def _estimate_history_command(entry: _HistoryCommand, seen: set[int]) -> int:
    # Sparse raster commands can declare a payload size. Do not traverse the
    # object graph of opaque legacy or plugin commands.
    total = _ENTRY_OVERHEAD + _OPAQUE_OBJECT_OVERHEAD
    try:
        retained = getattr(entry.command, "retained_bytes", None)
    except Exception:
        logger.exception("History command retained-byte estimate failed")
        return total
    if isinstance(retained, int) and retained >= 0 and id(entry.command) not in seen:
        seen.add(id(entry.command))
        total += retained
    return total


def _estimate_history_entry(entry: Any, seen: set[int]) -> int:
    key = id(entry)
    if key in seen:
        return 0
    seen.add(key)
    if isinstance(entry, _HistoryPatch):
        # Count the compressed payloads directly; the ``before``/``after``
        # properties would decompress and must never be called here.
        total = _ENTRY_OVERHEAD
        total += _bytes_len(entry._before_data, seen)
        total += _bytes_len(entry._after_data, seen)
        total += _estimate_generic(entry.metadata, seen, _MAX_DEPTH)
        return total
    if isinstance(entry, _ResizeEntry):
        total = _ENTRY_OVERHEAD
        for snapshot in (entry.before, entry.after):
            snap_key = id(snapshot)
            if snap_key in seen:
                continue
            seen.add(snap_key)
            for item in snapshot.layer_images:
                total += _bytes_len(item[1], seen)
            total += _estimate_generic(snapshot.layer_vectors, seen, _MAX_DEPTH)
            # Image Size snapshots (CT-13) also retain masks and the selection.
            for attribute, legacy in (
                ("layer_masks", "layer_masks"),
                ("filter_masks", "effect_masks"),
            ):
                for item in getattr(snapshot, attribute, getattr(snapshot, legacy, None)) or ():
                    total += _bytes_len(item[1], seen)
            selection = getattr(snapshot, "selection", None)
            if selection:
                total += _bytes_len(selection[0], seen)
        return total
    if isinstance(entry, _MergeEntry):
        total = _ENTRY_OVERHEAD
        total += _bytes_len(entry.source_image_data, seen)
        total += _bytes_len(entry.target_image_before, seen)
        return total
    if isinstance(entry, (_MergeVisibleEntry, _FlattenAllEntry)):
        return _ENTRY_OVERHEAD + _bytes_len(entry.result_image_data, seen)
    # Other commands and unknown entries retain their opaque bookkeeping cost.
    return (
        _estimate_history_command(entry, seen)
        if isinstance(entry, _HistoryCommand)
        else _OPAQUE_OBJECT_OVERHEAD
    )


def estimate_history_group_bytes(group: Any, *, seen: set[int]) -> int:
    """Approximate the retained bytes of one detached ``_HistoryGroup``."""
    if group is None:
        return 0
    key = id(group)
    if key in seen:
        return 0
    seen.add(key)
    total = _CONTAINER_OVERHEAD
    for entry in getattr(group, "entries", None) or ():
        total += _estimate_history_entry(entry, seen)
    return total


def estimate_page_history_bundle_bytes(bundle: Any, *, seen: set[int]) -> int:
    """Approximate the retained bytes of a detached ``PageHistoryBundle``."""
    if bundle is None:
        return 0
    key = id(bundle)
    if key in seen:
        return 0
    seen.add(key)
    total = _ENTRY_OVERHEAD
    for stack_name in ("undo_groups", "redo_groups"):
        for group in getattr(bundle, stack_name, None) or ():
            total += estimate_history_group_bytes(group, seen=seen)
    return total


__all__ = [
    "estimate_history_group_bytes",
    "estimate_metadata_payload_bytes",
    "estimate_page_history_bundle_bytes",
    "estimate_page_snapshot_bytes",
]
