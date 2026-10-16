"""Bounded-memory slice rasteriser (CT-12 spec 12 §10 / §13).

Never materialises the whole stream.  A slice buffer is composed from
the spans that overlap it; each span's page is fetched through the
``page_rgba`` callback, cropped, scaled **once as a whole** to the stream
width (Pillow, premultiplied ``RGBa`` LANCZOS — the CT-13 kernel) and
kept in a tiny LRU so consecutive slices reuse it.  Because a page is
scaled exactly once and slices only copy rows, concatenating the slices
reproduces the scaled stream pixel-for-pixel (spec 12 §17).  Rows that
belong to no span are gap: matte colour when given, else transparent.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from .stream_span import StreamSpan
from .vertical_stream_plan import VerticalStreamPlan

__all__ = ["StreamStripeComposer"]

PageRgba = Callable[[str], NDArray[np.uint8]]


class StreamStripeComposer:
    """Compose ``[y0, y1)`` of the stream into a straight-alpha RGBA8 buffer."""

    def __init__(
        self,
        stream: VerticalStreamPlan,
        *,
        page_rgba: PageRgba,
        matte_rgba: tuple[int, int, int, int] | None = None,
        cache_pages: int = 2,
        resample: Image.Resampling = Image.Resampling.LANCZOS,
    ) -> None:
        if cache_pages < 1:
            raise ValueError("cache_pages must be >= 1")
        self._stream = stream
        self._page_rgba = page_rgba
        self._matte = matte_rgba
        self._cache_pages = cache_pages
        self._resample = resample
        self._scaled: OrderedDict[int, NDArray[np.uint8]] = OrderedDict()
        self.page_fetches = 0

    @property
    def stream(self) -> VerticalStreamPlan:
        return self._stream

    def compose(self, y0: int, y1: int) -> NDArray[np.uint8]:
        if y0 < 0 or y1 > self._stream.height or y1 <= y0:
            raise ValueError(f"slice [{y0}, {y1}) is outside the stream")
        width = self._stream.width
        out = np.zeros((y1 - y0, width, 4), dtype=np.uint8)
        if self._matte is not None:
            out[:, :] = np.asarray(self._matte, dtype=np.uint8)
        for span in self._stream.spans_between(y0, y1):
            scaled = self._scaled_span(span)
            top = max(y0, span.stream_y0)
            bottom = min(y1, span.stream_y1)
            out[top - y0 : bottom - y0] = scaled[top - span.stream_y0 : bottom - span.stream_y0]
        return out

    def _scaled_span(self, span: StreamSpan) -> NDArray[np.uint8]:
        cached = self._scaled.get(span.index)
        if cached is not None:
            self._scaled.move_to_end(span.index)
            return cached
        self.page_fetches += 1
        page = np.asarray(self._page_rgba(span.page_id))
        if page.ndim != 3 or page.shape[2] != 4 or page.dtype != np.uint8:
            raise ValueError(f"page {span.page_id} must be straight-alpha RGBA8 (H x W x 4)")
        x, y, w, h = span.source_crop
        if y + h > page.shape[0] or x + w > page.shape[1]:
            raise ValueError(f"page {span.page_id} is smaller than its segment crop")
        cropped = page[y : y + h, x : x + w]
        scaled = self.scale_rgba(cropped, self._stream.width, span.height, self._resample)
        self._scaled[span.index] = scaled
        while len(self._scaled) > self._cache_pages:
            self._scaled.popitem(last=False)
        return scaled

    @staticmethod
    def scale_rgba(
        rgba: NDArray[np.uint8],
        width: int,
        height: int,
        resample: Image.Resampling = Image.Resampling.LANCZOS,
    ) -> NDArray[np.uint8]:
        """Premultiplied resize of straight RGBA8 to ``width`` x ``height``."""
        source = np.ascontiguousarray(rgba)
        if source.shape[1] == width and source.shape[0] == height:
            return source.copy()
        image = Image.fromarray(source, mode="RGBA").convert("RGBa")
        resized = image.resize((int(width), int(height)), resample=resample).convert("RGBA")
        return np.asarray(resized, dtype=np.uint8).copy()
