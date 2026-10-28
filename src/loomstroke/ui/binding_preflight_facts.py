"""Visible text/balloon page-space bounds, separate from existing CT-16 facts."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..core.binding_danger_geometry import rotated_bounds, transformed_bounds
from ..core.japanese_text.typography import has_typography
from ..core.layer_transform_metadata import read_layer_transform
from ..core.vector_text_layout import TextLayoutCalculator
from ..plugins.comic_tools.model import balloon_record_from_object


def binding_objects(layers: Iterable[Any]) -> tuple:
    result = []
    for layer in layers:
        if not getattr(layer, "visible", True) or getattr(layer, "opacity", 1.0) <= 0:
            continue
        transform = read_layer_transform(
            getattr(layer, "metadata", None), getattr(layer, "position", (0, 0))
        ).to_affine()
        for obj in getattr(layer, "vector_objects", ()) or ():
            record = balloon_record_from_object(obj)
            payload = TextLayoutCalculator.extract_text_payload(obj)
            bounds = None
            relevant = record is not None
            if record is not None:
                x, y, w, h = record.body_rect
                bounds = (x, y, x + w, y + h)
            elif (
                payload is not None
                and TextLayoutCalculator.text_content_from_payload(payload).strip()
            ):
                relevant = True
                bounds = TextLayoutCalculator.text_bbox_from_payload(payload)
                if bounds is not None:
                    bounds = rotated_bounds(
                        bounds, TextLayoutCalculator.text_rotation_from_payload(payload)
                    )
                if has_typography(payload):
                    # CT-16 never treats unverified ink bounds as a pass.
                    bounds = None
            if bounds is not None:
                result.append((str(obj.id), str(layer.id), transformed_bounds(bounds, transform)))
            elif relevant:
                result.append((str(obj.id), str(layer.id), None))
        result.extend(binding_objects(getattr(layer, "children", ()) or ()))
    return tuple(result)
