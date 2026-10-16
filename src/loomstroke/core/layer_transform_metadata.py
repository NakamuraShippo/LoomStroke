"""Committed layer transforms in the existing optional metadata boundary.

The archive schema and native layer image stay unchanged. Old layers use
their integer position; new snapshots additionally retain the full transform.
Transient transform previews are deliberately excluded.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Any

from .command_layer_access import _layer_transform
from .command_types import LayerTransform

logger = logging.getLogger(__name__)

LAYER_TRANSFORM_NAMESPACE = "loomstroke.layer_transform"
_SCHEMA_VERSION = 1
_FIELDS = ("tx", "ty", "scale_x", "scale_y", "rotation", "origin_x", "origin_y")


def capture_layer_transform_metadata(layer: Any) -> dict[str, Any]:
    """Copy metadata with the current committed transform, never a preview."""

    raw = getattr(layer, "metadata", None)
    metadata = dict(raw) if isinstance(raw, Mapping) else {}
    transform = _layer_transform(layer)
    raw_transform = getattr(layer, "transform", None)
    if _has_frame_paint_basis(metadata) and isinstance(raw_transform, LayerTransform):
        transform = raw_transform
    if transform == LayerTransform.identity():
        # A bake or undo may have left old transform metadata on the live
        # layer. Recapture must never resurrect that obsolete transform.
        metadata.pop(LAYER_TRANSFORM_NAMESPACE, None)
        return metadata

    values = {name: float(getattr(transform, name)) for name in _FIELDS}
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("Cannot snapshot a non-finite layer transform")
    metadata[LAYER_TRANSFORM_NAMESPACE] = {"schema_version": _SCHEMA_VERSION, **values}
    return metadata


def read_layer_transform(
    metadata: Mapping[str, Any] | None,
    position: tuple[int, int],
) -> LayerTransform:
    """Decode supported finite values, falling back to legacy placement."""

    fallback = LayerTransform.translation(*position)
    # A prepared raster frame edge uses integer offsets for its image origin,
    # while its registered paint basis requires an identity layer transform.
    # Treating those offsets as a transform makes page-switch/save reloads
    # reject the next comic edit as an inconsistent paint basis.
    if _has_frame_paint_basis(metadata):
        fallback = LayerTransform.identity()
    if not isinstance(metadata, Mapping) or LAYER_TRANSFORM_NAMESPACE not in metadata:
        return fallback
    payload = metadata[LAYER_TRANSFORM_NAMESPACE]
    if isinstance(payload, Mapping):
        version = payload.get("schema_version")
        if type(version) is int and version == _SCHEMA_VERSION:
            values: dict[str, float] = {}
            for name in _FIELDS:
                raw = payload.get(name)
                if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                    break
                try:
                    value = float(raw)
                except OverflowError:
                    break
                if not math.isfinite(value):
                    break
                values[name] = value
            if len(values) == len(_FIELDS):
                return LayerTransform(**values)
    logger.warning("Ignoring unsupported or invalid layer transform metadata; using layer position")
    return fallback


def _has_frame_paint_basis(metadata: Mapping[str, Any] | None) -> bool:
    part = (
        metadata.get("loomstroke.comictools.frame_part") if isinstance(metadata, Mapping) else None
    )
    return (
        isinstance(part, Mapping)
        and type(part.get("version")) is int
        and part.get("version") == 1
        and part.get("role") == "edge"
        and isinstance(part.get("paint_basis_origin"), (list, tuple))
        and len(part["paint_basis_origin"]) == 2
    )
