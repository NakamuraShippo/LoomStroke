"""Capture ownership for Move previews that replace committed model fields."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any

from ..common.document_capture_guard import (
    DocumentCaptureLease,
    begin_document_capture_edit,
    release_document_capture_edit,
)

logger = logging.getLogger(__name__)


def _vector_restored(actual: Any, original: Any, layer_id: Any) -> bool:
    if actual == original:
        return True
    metadata = getattr(original, "meta", None)
    if isinstance(metadata, dict) and "layer_id" not in metadata:
        # The existing Canvas.replace_vector_object backfills only this
        # ownership hint for older objects. Do not mistake that normalization
        # for an unrestored preview, or ignore any other metadata changes.
        return actual == replace(original, meta={**metadata, "layer_id": layer_id})
    return False


@dataclass(frozen=True)
class _LayerOriginal:
    layer: Any
    offset: tuple[Any, Any]
    mask: Any
    vectors: tuple[Any, ...]

    @classmethod
    def capture(cls, layer: Any) -> _LayerOriginal:
        # Move replaces mask/images and vector objects; retaining the old
        # values avoids another full-canvas pixel copy for this readiness check.
        return cls(
            layer,
            (getattr(layer, "offset_x", None), getattr(layer, "offset_y", None)),
            getattr(layer, "layer_mask", None),
            tuple(getattr(layer, "vector_objects", ()) or ()),
        )

    def restored(self) -> bool:
        vectors = tuple(getattr(self.layer, "vector_objects", ()) or ())
        return (
            (getattr(self.layer, "offset_x", None), getattr(self.layer, "offset_y", None))
            == self.offset
            and getattr(self.layer, "layer_mask", None) == self.mask
            and len(vectors) == len(self.vectors)
            and all(
                _vector_restored(actual, original, getattr(self.layer, "id", None))
                for actual, original in zip(vectors, self.vectors, strict=True)
            )
        )


class MoveLiveCapture:
    """Keep an unresolved lease even when pointer state is discarded."""

    def __init__(self) -> None:
        self._lease: DocumentCaptureLease | None = None
        self._originals: list[_LayerOriginal] = []

    def begin(self, canvas: Any, layers: list[Any]) -> bool:
        try:
            lease = begin_document_capture_edit(canvas, self._lease)
        except Exception:
            logger.exception("Move preview could not acquire its document capture guard")
            return False
        self._lease = lease
        pending = list(layers)
        seen = set()
        self._originals = []
        while pending:
            layer = pending.pop()
            if layer is None or id(layer) in seen:
                continue
            seen.add(id(layer))
            self._originals.append(_LayerOriginal.capture(layer))
            pending.extend(getattr(layer, "children", ()) or ())
        return True

    def finish_restore(self) -> bool:
        if self._lease is None:
            return True
        try:
            if not all(original.restored() for original in self._originals):
                return False
        except Exception:
            logger.exception("Move preview restoration could not be verified")
            return False
        if not release_document_capture_edit(self._lease):
            return False
        self._lease = None
        self._originals = []
        return True
