"""Simplify / smooth / straighten / offset on ``VectorObject`` paths, keeping stable ids.

Geometry lives in ``loomstroke.core.vector_correction``; this adapter maps anchor
positions in and out, keeps handles of surviving anchors, and remaps
``loomstroke:ids`` so kept anchors keep their ids (new ones are issued by
``ensure_vector_element_ids``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from loomstroke.core.vector_correction import (
    PathCorrectionResult,
    PathOffsetter,
    PathSimplifier,
    PathSmoother,
    PathStraightener,
)

from .common import (
    VECTOR_IDS_META_KEY,
    Anchor,
    VectorObject,
    VectorPath,
    clone_vector_object_with_patch,
    ensure_vector_element_ids,
)

__all__ = ["PathCorrectionService"]

Point = tuple[float, float]
Operation = Callable[[Sequence[Point], bool], PathCorrectionResult]


class PathCorrectionService:
    def simplify(self, obj: VectorObject, tolerance: float) -> VectorObject | None:
        simplifier = PathSimplifier()
        return self._apply(
            obj, lambda pts, closed: simplifier.simplify(pts, tolerance, closed=closed)
        )

    def smooth(
        self, obj: VectorObject, *, strength: float = 0.5, iterations: int = 1
    ) -> VectorObject | None:
        smoother = PathSmoother()
        return self._apply(
            obj,
            lambda pts, closed: smoother.smooth(
                pts, strength=strength, iterations=iterations, closed=closed
            ),
        )

    def straighten(self, obj: VectorObject, *, amount: float = 1.0) -> VectorObject | None:
        straightener = PathStraightener()
        return self._apply(obj, lambda pts, _closed: straightener.straighten(pts, amount=amount))

    def offset(self, obj: VectorObject, distance: float) -> VectorObject | None:
        offsetter = PathOffsetter(miter_limit=float(getattr(obj.style, "miter_limit", 4.0) or 4.0))
        return self._apply(obj, lambda pts, closed: offsetter.offset(pts, distance, closed=closed))

    # -- internals ---------------------------------------------------------------------

    def _apply(self, obj: VectorObject, operation: Operation) -> VectorObject | None:
        new_paths: list[VectorPath] = []
        kept_per_path: list[tuple[int, ...]] = []
        changed = False
        for path in obj.paths:
            anchors = list(path.anchors)
            positions = [(float(a.pos[0]), float(a.pos[1])) for a in anchors]
            result = operation(positions, bool(path.closed))
            if result.issues or len(result.points) < 2:
                new_paths.append(path)
                kept_per_path.append(tuple(range(len(anchors))))
                continue
            kept = result.kept_indices if len(result.kept_indices) == len(result.points) else ()
            rebuilt: list[Anchor] = []
            for index, point in enumerate(result.points):
                source = anchors[kept[index]] if kept else None
                rebuilt.append(
                    Anchor(
                        pos=point,
                        kind=source.kind if source else "corner",
                        h_in=source.h_in if source else None,
                        h_out=source.h_out if source else None,
                    )
                )
            if [a.pos for a in rebuilt] != positions or len(rebuilt) != len(anchors):
                changed = True
            new_paths.append(VectorPath(anchors=rebuilt, closed=path.closed))
            kept_per_path.append(kept)
        if not changed:
            return None
        meta = self._remap_ids(obj.meta, kept_per_path)
        patched = clone_vector_object_with_patch(obj, paths=new_paths, meta=meta)
        return ensure_vector_element_ids(patched)

    @staticmethod
    def _remap_ids(meta: Mapping[str, Any], kept_per_path: list[tuple[int, ...]]) -> dict[str, Any]:
        merged = dict(meta)
        raw_ids = merged.get(VECTOR_IDS_META_KEY)
        if not isinstance(raw_ids, Mapping):
            return merged
        ids_meta = dict(raw_ids)
        raw_paths = ids_meta.get("paths")
        if not isinstance(raw_paths, Sequence) or isinstance(raw_paths, (str, bytes)):
            return merged
        paths_meta = []
        for path_index, raw_path in enumerate(raw_paths):
            if not isinstance(raw_path, Mapping):
                paths_meta.append(raw_path)
                continue
            path_meta = dict(raw_path)
            raw_anchors = path_meta.get("anchors")
            kept = kept_per_path[path_index] if path_index < len(kept_per_path) else ()
            if isinstance(raw_anchors, Sequence) and not isinstance(raw_anchors, (str, bytes)):
                anchors_meta = list(raw_anchors)
                if kept:
                    path_meta["anchors"] = [
                        (
                            dict(anchors_meta[i])
                            if i < len(anchors_meta) and isinstance(anchors_meta[i], Mapping)
                            else {}
                        )
                        for i in kept
                    ]
                else:
                    path_meta["anchors"] = []  # topology unknown: fresh anchor ids
            paths_meta.append(path_meta)
        ids_meta["paths"] = paths_meta
        merged[VECTOR_IDS_META_KEY] = ids_meta
        return merged
