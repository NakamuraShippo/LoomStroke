"""
Flood fill implementation for raster layers.

Provides scanline flood fill with optional gap-closing preprocessing.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

Span = tuple[int, int, int]


@dataclass(frozen=True)
class FloodFillResult:
    spans: list[Span]
    bounds: tuple[int, int, int, int] | None


def flood_fill_spans(
    reference: NDArray[Any],
    seed_x: int,
    seed_y: int,
    *,
    tolerance: int,
    gap_close_px: int,
    connectivity: int = 8,
    allowed: NDArray[Any] | None = None,
) -> FloodFillResult:
    if connectivity != 8:
        raise ValueError("Only 8-connectivity is supported")

    data = _ensure_rgba_uint8(reference)
    height, width = data.shape[:2]
    if seed_x < 0 or seed_y < 0 or seed_x >= width or seed_y >= height:
        return FloodFillResult([], None)

    tol = max(0, min(int(tolerance), 255))
    gap = max(0, int(gap_close_px))
    seed = data[seed_y, seed_x]

    similar = _similarity_mask(data, seed, tol)
    fillable = _apply_gap_close(similar, gap) if gap > 0 else similar
    if allowed is not None:
        if allowed.shape != (height, width):
            raise ValueError("Fill coverage must have the same size as the reference.")
        fillable = fillable & (allowed > 0)

    if not fillable[seed_y, seed_x]:
        return FloodFillResult([], None)

    spans = _scanline_fill(fillable, seed_x, seed_y)
    bounds = _bounds_from_spans(spans)
    return FloodFillResult(spans, bounds)


def _ensure_rgba_uint8(reference: NDArray[Any]) -> NDArray[Any]:
    data = np.asarray(reference)
    if data.ndim != 3 or data.shape[2] not in (3, 4):
        raise ValueError("reference must be an HxWx3 or HxWx4 array")

    if data.dtype != np.uint8:
        data = np.clip(data, 0, 255).astype(np.uint8)

    if data.shape[2] == 3:
        height, width = data.shape[:2]
        alpha = np.full((height, width, 1), 255, dtype=np.uint8)
        data = np.concatenate([data, alpha], axis=2)

    return data


def _similarity_mask(reference: NDArray[Any], seed: NDArray[Any], tolerance: int) -> NDArray[Any]:
    ref_int = reference.astype(np.int16, copy=False)
    seed_int = seed.astype(np.int16, copy=False)
    diff = np.abs(ref_int - seed_int)
    dist = diff.max(axis=2)
    return cast("NDArray[Any]", dist <= tolerance)


def _apply_gap_close(similar: NDArray[Any], radius: int) -> NDArray[Any]:
    boundary = ~similar
    closed = _erode(_dilate(boundary, radius), radius)
    return cast("NDArray[Any]", similar & ~closed)


try:
    from scipy.ndimage import binary_dilation, binary_erosion  # noqa: PLC0415

    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


def _dilate(mask: NDArray[Any], radius: int) -> NDArray[Any]:
    out = mask.astype(bool, copy=False)
    if _HAS_SCIPY:
        struct = np.ones((2 * radius + 1, 2 * radius + 1), dtype=bool)
        return cast("NDArray[Any]", binary_dilation(out, structure=struct, iterations=1))
    for _ in range(radius):
        padded = np.pad(out, 1, mode="constant", constant_values=False)
        out = (
            padded[:-2, :-2]
            | padded[:-2, 1:-1]
            | padded[:-2, 2:]
            | padded[1:-1, :-2]
            | padded[1:-1, 1:-1]
            | padded[1:-1, 2:]
            | padded[2:, :-2]
            | padded[2:, 1:-1]
            | padded[2:, 2:]
        )
    return out


def _erode(mask: NDArray[Any], radius: int) -> NDArray[Any]:
    if _HAS_SCIPY:
        out = mask.astype(bool, copy=False)
        struct = np.ones((2 * radius + 1, 2 * radius + 1), dtype=bool)
        return cast("NDArray[Any]", binary_erosion(out, structure=struct, iterations=1))
    return ~_dilate(~mask, radius)


def _scanline_fill(fillable: NDArray[Any], seed_x: int, seed_y: int) -> list[Span]:
    """Collect the filled spans, preferring the compiled implementation.

    The pure-Python span walk below indexes the mask one pixel at a time, which
    costs roughly 330 ms per megapixel -- 0.7 s at 1080p and 2.7 s at 4K, well
    past the interactive budget. The numba build does the same walk natively;
    it is a drop-in replacement, so any failure to compile just falls back.
    """
    if _numba_scanline_fill is not None:
        try:
            return _scanline_fill_compiled(fillable, seed_x, seed_y)
        except Exception:  # pragma: no cover - compilation/runtime guard
            logger.debug("Compiled flood fill unavailable; using Python", exc_info=True)
    return _scanline_fill_python(fillable, seed_x, seed_y)


def _scanline_fill_compiled(fillable: NDArray[Any], seed_x: int, seed_y: int) -> list[Span]:
    assert _numba_scanline_fill is not None
    mask = np.ascontiguousarray(fillable, dtype=np.uint8)
    spans_arr, count = _numba_scanline_fill(mask, int(seed_x), int(seed_y))
    return [(int(row[0]), int(row[1]), int(row[2])) for row in spans_arr[:count]]


def _scanline_fill_python(fillable: NDArray[Any], seed_x: int, seed_y: int) -> list[Span]:
    height, width = fillable.shape
    visited = np.zeros_like(fillable, dtype=bool)
    spans: list[Span] = []
    stack: list[tuple[int, int]] = [(seed_x, seed_y)]

    while stack:
        x, y = stack.pop()
        if y < 0 or y >= height or x < 0 or x >= width:
            continue
        if visited[y, x] or not fillable[y, x]:
            continue

        x0 = x
        while x0 > 0 and fillable[y, x0 - 1] and not visited[y, x0 - 1]:
            x0 -= 1
        x1 = x
        while x1 < width - 1 and fillable[y, x1 + 1] and not visited[y, x1 + 1]:
            x1 += 1

        visited[y, x0 : x1 + 1] = True
        spans.append((y, x0, x1))

        for ny in (y - 1, y + 1):
            if ny < 0 or ny >= height:
                continue
            nx = x0 - 1
            nx_end = x1 + 1
            while nx <= nx_end:
                if 0 <= nx < width and fillable[ny, nx] and not visited[ny, nx]:
                    stack.append((nx, ny))
                    nx += 1
                    while nx <= nx_end and 0 <= nx < width:
                        if not fillable[ny, nx] or visited[ny, nx]:
                            break
                        nx += 1
                    continue
                nx += 1

    return spans


def _build_numba_scanline_fill() -> Any | None:  # noqa: PLR0915 - mirrors the Python walk
    """Compile the span walk with numba, or return ``None`` when unavailable.

    Mirrors ``_scanline_fill_python`` statement for statement, including the
    LIFO stack order, so the emitted span list is identical -- consumers rely
    on the order, and the equivalence is pinned by
    ``tests/unit/test_flood_fill_jit_parity.py``.
    """
    try:
        from numba import njit  # noqa: PLC0415 - optional acceleration dependency
    except Exception:
        return None

    @njit(cache=True, nogil=True)  # type: ignore[misc]
    def _scanline(  # noqa: PLR0912, PLR0915 - kept inline and 1:1 with the Python walk
        mask: Any, seed_x: int, seed_y: int
    ) -> Any:
        height, width = mask.shape
        visited = np.zeros((height, width), dtype=np.uint8)

        stack_cap = 1024
        stack_x = np.empty(stack_cap, dtype=np.int32)
        stack_y = np.empty(stack_cap, dtype=np.int32)
        stack_x[0] = seed_x
        stack_y[0] = seed_y
        sp = 1

        span_cap = 1024
        spans = np.empty((span_cap, 3), dtype=np.int32)
        span_count = 0

        while sp > 0:
            sp -= 1
            x = stack_x[sp]
            y = stack_y[sp]
            if y < 0 or y >= height or x < 0 or x >= width:
                continue
            if visited[y, x] != 0 or mask[y, x] == 0:
                continue

            x0 = x
            while x0 > 0 and mask[y, x0 - 1] != 0 and visited[y, x0 - 1] == 0:
                x0 -= 1
            x1 = x
            while x1 < width - 1 and mask[y, x1 + 1] != 0 and visited[y, x1 + 1] == 0:
                x1 += 1

            for xi in range(x0, x1 + 1):
                visited[y, xi] = 1

            if span_count >= span_cap:
                span_cap *= 2
                grown = np.empty((span_cap, 3), dtype=np.int32)
                grown[:span_count] = spans[:span_count]
                spans = grown
            spans[span_count, 0] = y
            spans[span_count, 1] = x0
            spans[span_count, 2] = x1
            span_count += 1

            for step in range(2):
                ny = y - 1 if step == 0 else y + 1
                if ny < 0 or ny >= height:
                    continue
                nx = x0 - 1
                nx_end = x1 + 1
                while nx <= nx_end:
                    if 0 <= nx < width and mask[ny, nx] != 0 and visited[ny, nx] == 0:
                        if sp >= stack_cap:
                            stack_cap *= 2
                            gx = np.empty(stack_cap, dtype=np.int32)
                            gy = np.empty(stack_cap, dtype=np.int32)
                            gx[:sp] = stack_x[:sp]
                            gy[:sp] = stack_y[:sp]
                            stack_x = gx
                            stack_y = gy
                        stack_x[sp] = nx
                        stack_y[sp] = ny
                        sp += 1
                        nx += 1
                        while nx <= nx_end and 0 <= nx < width:
                            if mask[ny, nx] == 0 or visited[ny, nx] != 0:
                                break
                            nx += 1
                        continue
                    nx += 1

        return spans, span_count

    return _scanline


_numba_scanline_fill = _build_numba_scanline_fill()


def _bounds_from_spans(spans: Iterable[Span]) -> tuple[int, int, int, int] | None:
    spans_list = list(spans)
    if not spans_list:
        return None
    min_x = min(span[1] for span in spans_list)
    max_x = max(span[2] for span in spans_list)
    min_y = min(span[0] for span in spans_list)
    max_y = max(span[0] for span in spans_list)
    return (min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


__all__ = ["FloodFillResult", "Span", "flood_fill_spans"]
