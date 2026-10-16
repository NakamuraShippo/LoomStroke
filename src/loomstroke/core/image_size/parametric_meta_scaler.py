"""Scale the page-space ``reference_bbox`` of parametric shapes (CT-13 R3).

Parametric shape params (balloon tail ratios, focus-line counts, …) are
normalized to the bbox and therefore scale-invariant; only the bbox itself
lives in canvas pixels.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class ParametricMetaScaler:
    """Return a scaled copy of a parametric shape's metadata."""

    META_KEY = "shape_manager_parametric"

    def scale(self, meta: Mapping[str, Any], sx: float, sy: float) -> dict[str, Any] | None:
        """Scaled copy of *meta*, or ``None`` when there is no bbox to scale."""
        payload = meta.get(self.META_KEY) if isinstance(meta, Mapping) else None
        if not isinstance(payload, Mapping):
            return None
        bbox = payload.get("reference_bbox")
        if not isinstance(bbox, Sequence) or isinstance(bbox, (str, bytes)) or len(bbox) != 4:
            return None
        try:
            values = [float(item) for item in bbox]
        except (TypeError, ValueError):
            return None
        new_payload = dict(payload)
        new_payload["reference_bbox"] = [
            values[0] * float(sx),
            values[1] * float(sy),
            values[2] * float(sx),
            values[3] * float(sy),
        ]
        new_meta = dict(meta)
        new_meta[self.META_KEY] = new_payload
        return new_meta
