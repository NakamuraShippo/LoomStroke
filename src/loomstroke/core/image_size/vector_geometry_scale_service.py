"""Scale vector objects (paths, handles, stroke, metadata) for Image Size.

Anchors and bezier handles follow the per-axis scale; stroke width and
dash pattern follow the uniform (geometric-mean) scale so line art keeps
its weight relative to the artwork (CT-13 spec §10.3).  Objects are
rebuilt as new frozen instances so that undo snapshots taken before the
scale stay valid — the same contract as the canvas flip / rotate path.
"""

from __future__ import annotations

import math
from typing import Any

from loomstroke.core.canvas_geometry_service import CanvasGeometryService

from .vector_meta_scaler_registry import VectorMetaScalerRegistry


class VectorGeometryScaleService:
    """Return scaled copies of ``layer.vector_objects`` entries."""

    def __init__(
        self,
        *,
        meta_registry: VectorMetaScalerRegistry | None = None,
        scale_stroke_width: bool = True,
    ) -> None:
        self._meta_registry = meta_registry
        self._scale_stroke_width = bool(scale_stroke_width)

    @property
    def meta_registry(self) -> VectorMetaScalerRegistry:
        if self._meta_registry is None:
            self._meta_registry = VectorMetaScalerRegistry.shared()
        return self._meta_registry

    @staticmethod
    def uniform_factor(sx: float, sy: float) -> float:
        return math.sqrt(abs(float(sx) * float(sy)))

    def scale_objects(self, objects: list[Any], sx: float, sy: float) -> list[Any]:
        if not objects:
            return objects
        if float(sx) == 1.0 and float(sy) == 1.0:
            return list(objects)
        return [self.scale_object(obj, sx, sy) for obj in objects]

    def scale_object(self, obj: Any, sx: float, sy: float) -> Any:
        updates: dict[str, Any] = {}
        paths = getattr(obj, "paths", None)
        if isinstance(paths, list):
            new_paths: list[Any] = []
            for path in paths:
                anchors = getattr(path, "anchors", None)
                if not anchors:
                    new_paths.append(path)
                    continue
                new_anchors = [self.scale_anchor(anchor, sx, sy) for anchor in anchors]
                new_paths.append(CanvasGeometryService.rebuild_like(path, {"anchors": new_anchors}))
            updates["paths"] = new_paths
        style = getattr(obj, "style", None)
        if style is not None and self._scale_stroke_width:
            new_style = self.scale_style(style, sx, sy)
            if new_style is not style:
                updates["style"] = new_style
        new_meta = self.meta_registry.scale_meta(getattr(obj, "meta", None), sx, sy)
        if new_meta is not None:
            updates["meta"] = new_meta
        if not updates:
            return obj
        return CanvasGeometryService.rebuild_like(obj, updates)

    def scale_anchor(self, anchor: Any, sx: float, sy: float) -> Any:
        pos = getattr(anchor, "pos", None)
        if not isinstance(pos, tuple) or len(pos) != 2:
            return anchor
        updates: dict[str, Any] = {"pos": (float(pos[0]) * sx, float(pos[1]) * sy)}
        for name in ("h_in", "h_out"):
            handle = getattr(anchor, name, None)
            if isinstance(handle, tuple) and len(handle) == 2:
                updates[name] = (float(handle[0]) * sx, float(handle[1]) * sy)
        return CanvasGeometryService.rebuild_like(anchor, updates)

    def scale_style(self, style: Any, sx: float, sy: float) -> Any:
        factor = self.uniform_factor(sx, sy)
        if factor == 1.0:
            return style
        updates: dict[str, Any] = {}
        stroke_width = getattr(style, "stroke_width", None)
        if isinstance(stroke_width, (int, float)) and not isinstance(stroke_width, bool):
            updates["stroke_width"] = float(stroke_width) * factor
        dash = getattr(style, "dash", None)
        if isinstance(dash, tuple) and dash:
            updates["dash"] = tuple(float(item) * factor for item in dash)
        if not updates:
            return style
        return CanvasGeometryService.rebuild_like(style, updates)
