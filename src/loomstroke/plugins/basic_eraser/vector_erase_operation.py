"""Detached vector eraser calculation with retained anchor ids and width profiles."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from loomstroke.core.fill.fill_gesture_mask import FillGestureMask
from loomstroke.core.vector_correction.vector_erase_geometry import VectorEraseGeometry

from ..vector_tools.common import clone_vector_object_with_patch, ensure_vector_element_ids
from ..vector_tools.stroke_profile import StrokeProfileAdapter


class VectorEraseOperation:
    @classmethod
    def calculate(
        cls, objects: list[Any], points: list[tuple[float, float]], size: float, mode: str
    ) -> list[Any] | None:
        area = FillGestureMask.path("Gap Fill", points, size)
        all_paths = [path for obj in objects for path in obj.paths]
        result = []
        changed = False
        for obj in objects:
            paths = []
            object_changed = False
            for path in obj.paths:
                others = [other for other in all_paths if other is not path]
                kept = VectorEraseGeometry.retained(path, area, mode, others, size / 4)
                if kept is None:
                    paths.append((path, 0.0, 1.0))
                else:
                    paths.extend((fragment.path, fragment.start, fragment.end) for fragment in kept)
                    object_changed = True
            if not object_changed:
                result.append(obj)
                continue
            changed = True
            for index, (path, start, end) in enumerate(paths):
                new_id = obj.id if index == 0 else f"{obj.id}-erase-{index}"
                existing_ids = {entry.id for entry in objects} | {entry.id for entry in result}
                while new_id in existing_ids and new_id != obj.id:
                    new_id += "-cut"
                meta = deepcopy(obj.meta)
                raw_ids = meta.get("loomstroke:ids")
                if isinstance(raw_ids, dict):
                    old = {
                        tuple(anchor.pos): entry
                        for old_path, ids in zip(obj.paths, raw_ids.get("paths", []), strict=False)
                        for anchor, entry in zip(
                            old_path.anchors, ids.get("anchors", []), strict=False
                        )
                    }
                    raw_ids["paths"] = [
                        {
                            "anchors": [
                                deepcopy(old.get(tuple(anchor.pos), {})) for anchor in path.anchors
                            ]
                        }
                    ]
                fragment = ensure_vector_element_ids(
                    clone_vector_object_with_patch(obj, id=new_id, paths=[path], meta=meta)
                )
                profile = StrokeProfileAdapter.read(obj)
                if profile is not None:
                    stops = [(0.0, profile.scale_at(start)), (1.0, profile.scale_at(end))]
                    stops.extend(
                        ((t - start) / max(1e-12, end - start), scale)
                        for t, scale in profile.curve
                        if start < t < end
                    )
                    fragment = StrokeProfileAdapter.write(fragment, profile.with_stops(stops))
                result.append(fragment)
        return result if changed else None
