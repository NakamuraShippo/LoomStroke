"""Image-only point intent: concept filtering and conservative mask corrections.

No model internals are changed. Corrections select connected signed differences
at newly supplied points; unrelated differences and positive anchors survive.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

Point = tuple[float, float, int]
MAX_CACHED_OBJECTS = 64


@dataclass
class PointMaskState:
    mask: np.ndarray
    points: tuple[Point, ...]


def parse_points(points: Any, labels: Any) -> tuple[Point, ...]:
    if not points or labels is None or len(points) != len(labels):
        raise ValueError("points and labels of equal length are required")
    result = []
    for pair, label in zip(points, labels, strict=True):
        if len(pair) != 2 or label not in (0, 1):  # noqa: PLR2004
            raise ValueError("each point requires two coordinates and a 0/1 label")
        x, y = map(float, pair)
        if not (np.isfinite(x) and np.isfinite(y) and 0 <= x <= 1 and 0 <= y <= 1):
            raise ValueError("point coordinates must be finite and normalized to [0, 1]")
        result.append((x, y, int(label)))
    return tuple(result)


def _pixel(point: Point, shape: tuple[int, ...]) -> tuple[int, int]:
    height, width = shape
    return min(height - 1, int(point[1] * height)), min(width - 1, int(point[0] * width))


def merge_points(previous: tuple[Point, ...], current: tuple[Point, ...]) -> tuple[Point, ...]:
    """Preserve earlier instructions; an explicit relabel at the same pixel wins."""
    labels = {(x, y): label for x, y, label in (*previous, *current)}
    return tuple((x, y, label) for (x, y), label in labels.items())


def load_base_mask(
    path: str, bbox: list[int], shape: tuple[int, ...], points: tuple[Point, ...]
) -> PointMaskState:
    """Restore the caller's selected asset, rather than a later cached revision."""
    from PIL import Image  # noqa: PLC0415

    if len(bbox) != 4 or any(not isinstance(value, int) for value in bbox):  # noqa: PLR2004
        raise ValueError("base mask requires an integer x/y/width/height bbox")
    x, y, width, height = bbox
    if min(x, y) < 0 or min(width, height) <= 0 or y + height > shape[0] or x + width > shape[1]:
        raise ValueError("base mask bounds exceed the source image")
    with Image.open(Path(path)) as image:
        if image.size != (width, height):
            raise ValueError("base mask dimensions do not match its bbox")
        coverage = np.minimum(
            np.asarray(image.convert("L")), np.asarray(image.convert("RGBA"))[..., 3]
        )
    mask = np.zeros(shape, dtype=bool)
    mask[y : y + height, x : x + width] = coverage > 0
    return PointMaskState(mask, points)


def filter_concepts(outputs: dict[str, Any], points: tuple[Point, ...]) -> dict[str, Any]:
    """Keep concepts satisfying every inclusion/exclusion point; never guess a noun."""
    masks = np.asarray(outputs["out_binary_masks"], dtype=bool)
    indices = [
        index
        for index, mask in enumerate(masks)
        if all(bool(mask[_pixel(point, mask.shape)]) == bool(point[2]) for point in points)
    ]
    return {
        **outputs,
        **{
            name: np.asarray(outputs[name])[indices]
            for name in ("out_binary_masks", "out_obj_ids", "out_probs", "out_boxes_xywh")
        },
    }


def _component(mask: np.ndarray, seed: tuple[int, int], *, diagonal: bool = False) -> np.ndarray:
    """Scanline fill; refinement uses four neighbours, scope cleanup also diagonals."""
    selected = np.zeros_like(mask, dtype=bool)
    pending = [seed]
    height, width = mask.shape
    while pending:
        y, x = pending.pop()
        if selected[y, x] or not mask[y, x]:
            continue
        row = mask[y] & ~selected[y]
        gaps = np.flatnonzero(~row)
        split = np.searchsorted(gaps, x)
        left = int(gaps[split - 1]) + 1 if split else 0
        right = int(gaps[split]) if split < len(gaps) else width
        selected[y, left:right] = True
        for neighbour in (y - 1, y + 1):
            if not 0 <= neighbour < height:
                continue
            start = max(0, left - int(diagonal))
            stop = min(width, right + int(diagonal))
            run = mask[neighbour, start:stop] & ~selected[neighbour, start:stop]
            starts = np.flatnonzero(run & ~np.r_[False, run[:-1]])
            pending.extend((neighbour, start + int(offset)) for offset in starts)
    return selected


def correct_mask(
    previous: PointMaskState, proposed: np.ndarray, points: tuple[Point, ...]
) -> np.ndarray:
    """Only apply signed changes at new points; keep existing positive anchors."""
    before = previous.mask
    if before.shape != proposed.shape:
        raise ValueError("refinement mask dimensions changed")
    result = before.copy()
    old_points = set(previous.points)
    # A repeated click is harmless; changed polarity is a new explicit instruction.
    current_labels = {(x, y): label for x, y, label in points}
    anchors = [
        point
        for point in (*previous.points, *points)
        if point[2] and current_labels.get(point[:2], point[2])
    ]
    exclusions = [p for p in merge_points(previous.points, points) if not p[2]]
    for point in points:
        if point in old_points:
            continue
        seed = _pixel(point, before.shape)
        positive = bool(point[2])
        delta = (proposed & ~before) if positive else (before & ~proposed)
        if not delta[seed]:
            continue
        component = _component(delta, seed)
        if not positive and any(component[_pixel(anchor, before.shape)] for anchor in anchors):
            continue
        if positive and any(component[_pixel(anchor, before.shape)] for anchor in exclusions):
            continue
        result[component] = positive
    return result


def stabilize_outputs(
    outputs: dict[str, Any],
    previous: PointMaskState | None,
    object_id: int,
    points: tuple[Point, ...],
) -> dict[str, Any]:
    """Filter to the requested object and update its geometry after correction."""
    ids = np.asarray(outputs["out_obj_ids"])
    indices = np.flatnonzero(ids == object_id)
    if len(indices) != 1:
        raise ValueError("refinement did not return the requested object")
    index = int(indices[0])
    mask = np.asarray(outputs["out_binary_masks"][index], dtype=bool)
    if previous is not None:
        mask = correct_mask(previous, mask, points)
    ys, xs = np.nonzero(mask)
    height, width = mask.shape
    box = (
        [
            float(xs.min()) / width,
            float(ys.min()) / height,
            float(xs.max() - xs.min() + 1) / width,
            float(ys.max() - ys.min() + 1) / height,
        ]
        if len(xs)
        else [0.0, 0.0, 0.0, 0.0]
    )
    return {
        **outputs,
        "out_binary_masks": np.asarray([mask]),
        "out_obj_ids": np.asarray([object_id]),
        "out_probs": np.asarray([outputs["out_probs"][index]]),
        "out_boxes_xywh": np.asarray([box]),
    }


def remember_outputs(
    state: dict[int, PointMaskState], outputs: dict[str, Any], points: tuple[Point, ...] = ()
) -> None:
    for object_id, mask in zip(outputs["out_obj_ids"], outputs["out_binary_masks"], strict=True):
        key = int(object_id)
        old = state.pop(key, None)
        # Retain previous positive anchors when a caller supplies only new points.
        labels = {(x, y): label for x, y, label in points}
        retained = tuple(p for p in old.points if p[:2] not in labels) if old else ()
        state[key] = PointMaskState(np.asarray(mask, dtype=bool).copy(), (*retained, *points))
        while len(state) > MAX_CACHED_OBJECTS:
            del state[next(iter(state))]
