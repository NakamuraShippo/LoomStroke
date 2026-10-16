"""Detached NumPy rendering for large fills; reuse existing flood and blend code."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np

from loomstroke.core.fill.commands_fill import (
    FloodFillCommand,
    PatternFillCommand,
    _SelectionClip,
)
from loomstroke.core.fill.flood_fill import (
    _apply_gap_close,
    _bounds_from_spans,
    _similarity_mask,
    flood_fill_spans,
)


def _four_connected(fillable: Any, seed_x: int, seed_y: int) -> list[tuple[int, int, int]]:
    height, width = fillable.shape
    visited = np.zeros((height, width), dtype=bool)
    stack = [(seed_x, seed_y)]
    spans = []
    while stack:
        x, y = stack.pop()
        if not 0 <= x < width or not 0 <= y < height or visited[y, x] or not fillable[y, x]:
            continue
        left = right = x
        while left > 0 and fillable[y, left - 1] and not visited[y, left - 1]:
            left -= 1
        while right < width - 1 and fillable[y, right + 1] and not visited[y, right + 1]:
            right += 1
        visited[y, left : right + 1] = True
        spans.append((y, left, right))
        for adjacent in (y - 1, y + 1):
            if not 0 <= adjacent < height:
                continue
            index = left
            while index <= right:
                if fillable[adjacent, index] and not visited[adjacent, index]:
                    stack.append((index, adjacent))
                    while (
                        index <= right
                        and fillable[adjacent, index]
                        and not visited[adjacent, index]
                    ):
                        index += 1
                else:
                    index += 1
    return spans


def _rotate_pattern(pattern: Any, angle: float, scale: float) -> Any:
    from PIL import Image  # noqa: PLC0415

    image = Image.fromarray(pattern)
    if scale != 1:
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.NEAREST,
        )
    if angle % 360:
        # An expanded rotation can double the bounding rectangle.
        radians = np.deg2rad(angle)
        width = abs(image.width * np.cos(radians)) + abs(image.height * np.sin(radians))
        height = abs(image.width * np.sin(radians)) + abs(image.height * np.cos(radians))
        if np.ceil(width) * np.ceil(height) > 16_777_216:
            raise ValueError("Rotated pattern exceeds 16,777,216 pixels.")
        image = image.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)
    return np.asarray(image).copy()


def render_fill(  # noqa: PLR0912, PLR0915 - detached span rendering
    snapshot: dict[str, Any], arguments: dict[str, Any], pattern: Any
) -> dict[str, Any]:
    reference = snapshot["reference"]
    reference_x, reference_y = snapshot["referenceOffset"]
    x, y = arguments["seed"]
    seed_x, seed_y = x - reference_x, y - reference_y
    if not 0 <= seed_x < reference.shape[1] or not 0 <= seed_y < reference.shape[0]:
        return {"changed": False}
    selection = snapshot["selection"]
    if selection is not None and not selection[y, x]:
        return {"changed": False}
    if arguments["connectivity"] == 8:
        filled = flood_fill_spans(
            reference,
            seed_x,
            seed_y,
            tolerance=arguments["tolerance"],
            gap_close_px=arguments["gapClose"],
        )
        spans, bounds = filled.spans, filled.bounds
    else:
        fillable = _similarity_mask(reference, reference[seed_y, seed_x], arguments["tolerance"])
        if arguments["gapClose"]:
            fillable = _apply_gap_close(fillable, arguments["gapClose"])
        spans = _four_connected(fillable, seed_x, seed_y)
        bounds = _bounds_from_spans(spans)
    if not spans or bounds is None:
        return {"changed": False}
    canvas_width, canvas_height = snapshot["canvasSize"]
    spans = [
        (row + reference_y, max(0, left + reference_x), min(canvas_width - 1, right + reference_x))
        for row, left, right in spans
        if 0 <= row + reference_y < canvas_height
        and right + reference_x >= 0
        and left + reference_x < canvas_width
    ]
    if not spans:
        return {"changed": False}
    bounds = _bounds_from_spans(spans)
    offset_x, offset_y = snapshot["targetOffset"]
    old = snapshot["target"]
    if arguments["target"] == "new_layer":
        offset_x = offset_y = 0
        output = np.zeros((canvas_height, canvas_width, 4), dtype=np.uint8)
    else:
        left, top = min(offset_x, bounds[0]), min(offset_y, bounds[1])
        right = max(offset_x + old.shape[1], bounds[0] + bounds[2])
        bottom = max(offset_y + old.shape[0], bounds[1] + bounds[3])
        if (right - left) * (bottom - top) > 16_777_216:
            raise ValueError("Expanded fill output exceeds 16,777,216 pixels.")
        output = np.zeros((bottom - top, right - left, 4), dtype=np.uint8)
        output[
            offset_y - top : offset_y - top + old.shape[0],
            offset_x - left : offset_x - left + old.shape[1],
        ] = old
        offset_x, offset_y = left, top
    target = SimpleNamespace(
        id=snapshot["layerId"],
        buffer=output,
        offset_x=offset_x,
        offset_y=offset_y,
        locked=False,
        layer_type="raster",
    )
    if pattern is None:
        command = FloodFillCommand(
            target.id,
            x,
            y,
            arguments["color"],
            arguments["tolerance"],
            arguments["gapClose"],
        )
    else:
        pattern = _rotate_pattern(pattern, arguments["rotation"], arguments["scale"])
        command = PatternFillCommand(
            target.id,
            x,
            y,
            pattern,
            arguments["tolerance"],
            arguments["gapClose"],
            pattern_offset=arguments["offset"],
        )
    command._spans, command._bounds = spans, bounds
    if selection is not None:
        command._selection_clip = _SelectionClip(selection, snapshot["selectionBounds"])
    saved_alpha = (
        output[:, :, 3].copy()
        if snapshot["alphaLock"] and arguments["target"] != "new_layer"
        else None
    )
    if isinstance(command, PatternFillCommand):
        command._apply_pattern_to_layer(None, target, record_backup=False)
    else:
        command._apply_to_layer(None, target, record_backup=False)
    if saved_alpha is not None:
        output[:, :, 3] = saved_alpha
        output[output[:, :, 3] == 0, :3] = 0
    same = (
        arguments["target"] != "new_layer"
        and (offset_x, offset_y) == snapshot["targetOffset"]
        and np.array_equal(old, output)
    )
    return {"changed": not same, "rgba": output, "offset": (offset_x, offset_y)}
