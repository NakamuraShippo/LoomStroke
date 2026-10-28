"""Cheap estimates for runtime layer payloads retained by creation commands.

This is a history-trimming heuristic, not RSS. Geometry charges each anchor
sequence by length, including a conservative allowance for position and both
handles. It never visits individual anchors, copies, or serializes payloads.
Only the existing vector paths/anchors/style/meta contract is inspected;
unknown objects retain the existing bounded metadata estimator's opaque cost.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtGui import QImage

_CONTAINER_BYTES = 64
_OBJECT_BYTES = 128
_ANCHOR_BYTES = 512
_STYLE_FIELDS = (
    "stroke_color",
    "stroke_width",
    "fill_enabled",
    "fill_color",
    "join",
    "cap",
    "miter_limit",
    "dash",
)


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray, memoryview)
    )


def _geometry_bytes(paths: Any, seen: set[int], metadata_bytes: Callable[..., int]) -> int:
    if id(paths) in seen:
        return 0
    seen.add(id(paths))
    total = _CONTAINER_BYTES
    for path in paths:
        if id(path) in seen:
            continue
        anchors = getattr(path, "anchors", None)
        if not _sequence(anchors):
            total += metadata_bytes(path, seen=seen)
            continue
        seen.add(id(path))
        total += _OBJECT_BYTES
        if id(anchors) not in seen:
            seen.add(id(anchors))
            total += _CONTAINER_BYTES + len(anchors) * _ANCHOR_BYTES
    return total


def _style_bytes(style: Any, seen: set[int], metadata_bytes: Callable[..., int]) -> int:
    if id(style) in seen:
        return 0
    if not hasattr(style, "stroke_color") or not hasattr(style, "stroke_width"):
        return metadata_bytes(style, seen=seen)
    seen.add(id(style))
    return _OBJECT_BYTES + sum(
        metadata_bytes(getattr(style, key, None), seen=seen) for key in _STYLE_FIELDS
    )


def _vector_bytes(objects: Any, seen: set[int], metadata_bytes: Callable[..., int]) -> int:
    if not _sequence(objects):
        return metadata_bytes(objects, seen=seen)
    if id(objects) in seen:
        return 0
    seen.add(id(objects))
    total = _CONTAINER_BYTES
    for vector in objects:
        if id(vector) in seen:
            continue
        paths = getattr(vector, "paths", None)
        if not _sequence(paths):
            total += metadata_bytes(vector, seen=seen)
            continue
        seen.add(id(vector))
        total += _OBJECT_BYTES + _geometry_bytes(paths, seen, metadata_bytes)
        total += _style_bytes(getattr(vector, "style", None), seen, metadata_bytes)
        total += metadata_bytes(getattr(vector, "meta", None), seen=seen)
    return total


def estimate_retained_layer_bytes(
    root: Any, *, seen: set[int] | None = None, image_keys: set[int] | None = None
) -> int:
    # The history-entry module imports Commands; keep this dependency lazy.
    from .page_history_memory import estimate_metadata_payload_bytes  # noqa: PLC0415

    total, pending = 0, [root]
    if seen is None:
        seen = set()
    if image_keys is None:
        image_keys = set()
    while pending:
        layer = pending.pop()
        if layer is None or id(layer) in seen:
            continue
        seen.add(id(layer))
        for key in ("image", "layer_mask", "filter_mask"):
            image = getattr(layer, key, None)
            if isinstance(image, QImage) and image.cacheKey() not in image_keys:
                image_keys.add(image.cacheKey())
                total += image.sizeInBytes()
        native = getattr(layer, "native_cmyk", None)
        if native is not None:
            for plane in (native.inks, native.alpha):
                if id(plane) not in seen:
                    seen.add(id(plane))
                    total += plane.nbytes
            total += estimate_metadata_payload_bytes(native.profile, seen=seen)
        total += _vector_bytes(
            getattr(layer, "vector_objects", None), seen, estimate_metadata_payload_bytes
        )
        total += estimate_metadata_payload_bytes(getattr(layer, "metadata", None), seen=seen)
        pending.extend(getattr(layer, "children", ()))
    return total
