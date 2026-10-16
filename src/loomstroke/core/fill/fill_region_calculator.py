"""Calculate fill regions without live canvas, layer, or Qt objects."""

from __future__ import annotations

import numpy as np

from .fill_region_request import FillRegionRequest
from .flood_fill import _apply_gap_close, _bounds_from_spans, flood_fill_spans


class FillRegionCalculator:
    @classmethod
    def calculate(
        cls, request: FillRegionRequest
    ) -> tuple[list[tuple[int, int, int]], tuple[int, int, int, int] | None]:
        reference = request.reference
        height, width = reference.shape[:2]
        x, y = request.seed
        if request.mode == "Bucket" and (not 0 <= x < width or not 0 <= y < height):
            return [], None
        if request.mode == "Bucket":
            result = flood_fill_spans(
                reference,
                x,
                y,
                tolerance=request.tolerance,
                gap_close_px=request.gap_close,
                allowed=request.allowed,
            )
            if not request.expansion and request.enclosure is None:
                return result.spans, result.bounds
            mask = np.zeros((height, width), dtype=np.uint8)
            for row, left, right in result.spans:
                mask[row, left : right + 1] = 255
        else:
            # Empty/white regions enclosed by actual line art; the gesture is
            # only a limit, so its edge never becomes a synthetic closing line.
            empty = (reference[:, :, 3] <= request.tolerance) | np.all(
                reference[:, :, :3] >= 255 - request.tolerance, axis=2
            )
            if request.gap_close:
                empty = _apply_gap_close(empty, request.gap_close)
            padded = np.pad(empty.astype(np.uint8) * 255, 1, constant_values=255)
            rgba = np.repeat(padded[:, :, None], 4, axis=2)
            exterior = flood_fill_spans(rgba, 0, 0, tolerance=0, gap_close_px=0)
            outside = np.zeros(padded.shape, dtype=bool)
            for row, left, right in exterior.spans:
                outside[row, left : right + 1] = True
            mask = (empty & ~outside[1:-1, 1:-1]).astype(np.uint8) * 255
        if request.expansion:
            mask = cls.expand(mask, request.expansion)
        if request.enclosure is not None:
            mask = np.minimum(mask, request.enclosure)
        if request.allowed is not None:
            mask = np.minimum(mask, request.allowed)
        spans = cls.spans(mask)
        return spans, _bounds_from_spans(spans)

    @staticmethod
    def expand(mask: np.ndarray, amount: int) -> np.ndarray:
        radius = min(16, abs(amount))
        output = mask.copy()
        reducer = np.maximum if amount > 0 else np.minimum
        for axis in (0, 1):
            pads = [(0, 0), (0, 0)]
            pads[axis] = (radius, radius)
            padded = np.pad(output, pads)
            result = np.zeros_like(output) if amount > 0 else np.full_like(output, 255)
            for shift in range(2 * radius + 1):
                slices = [slice(None), slice(None)]
                slices[axis] = slice(shift, shift + output.shape[axis])
                reducer(result, padded[tuple(slices)], out=result)
            output = result
        return output

    @staticmethod
    def spans(mask: np.ndarray) -> list[tuple[int, int, int]]:
        spans = []
        for row, values in enumerate(mask):
            edges = np.diff(np.pad((values > 0).astype(np.int8), 1))
            lefts, rights = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
            spans.extend(
                (row, int(left), int(right) - 1) for left, right in zip(lefts, rights, strict=True)
            )
        return spans
