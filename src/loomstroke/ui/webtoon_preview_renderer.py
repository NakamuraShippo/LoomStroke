"""Medium-resolution page thumbnails for the Webtoon preview (CT-12 §6.1 / §13).

Renders a page only when the preview asks for it (visible segments), at
full size through the shared offscreen path, then keeps a downscaled
QImage in a small LRU.  Nothing proportional to the whole stream is ever
allocated; pages hydrated for a thumbnail are handed back to the CT-08
budget right away.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from ..io.layer_io import DocumentMetadata
from .canvas.page_scene_snapshot import array_to_qimage
from .offscreen_page_renderer import OffscreenPageRenderer

logger = logging.getLogger(__name__)

__all__ = ["WebtoonPreviewRenderer"]

RenderPage = Callable[[DocumentMetadata, Any], np.ndarray]


class WebtoonPreviewRenderer:
    """``thumbnail(page_id) -> QImage | None`` with a bounded cache."""

    def __init__(
        self,
        page_service: Any,
        document_metadata: DocumentMetadata,
        *,
        render_page: RenderPage | None = None,
        max_width: int = 320,
        cache_size: int = 8,
    ) -> None:
        self._pages = page_service
        self._metadata = document_metadata
        self._offscreen: OffscreenPageRenderer | None = None
        self._render_page = render_page
        self._max_width = max(16, int(max_width))
        self._cache_size = max(1, int(cache_size))
        self._cache: OrderedDict[str, QImage | None] = OrderedDict()

    def thumbnail(self, page_id: str) -> QImage | None:
        if page_id in self._cache:
            self._cache.move_to_end(page_id)
            return self._cache[page_id]
        image = self._render_thumbnail(page_id)
        self._cache[page_id] = image
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return image

    def invalidate(self, page_id: str | None = None) -> None:
        if page_id is None:
            self._cache.clear()
        else:
            self._cache.pop(page_id, None)

    def release(self) -> None:
        self._cache.clear()
        offscreen, self._offscreen = self._offscreen, None
        if offscreen is not None:
            offscreen.release()

    def _render_thumbnail(self, page_id: str) -> QImage | None:
        try:
            page = self._pages.get_page_snapshot(page_id)
            if page is None:
                return None
            rgba = self._render(page)
            image = array_to_qimage(np.ascontiguousarray(rgba))
            if image.width() > self._max_width:
                image = image.scaledToWidth(
                    self._max_width, Qt.TransformationMode.SmoothTransformation
                )
            return image
        except Exception:
            logger.warning("webtoon preview render failed for page %s", page_id, exc_info=True)
            return None
        finally:
            evict = getattr(self._pages, "evict_pages_to_budget", None)
            if callable(evict):
                evict()

    def _render(self, page: Any) -> np.ndarray:
        if self._render_page is not None:
            return self._render_page(self._metadata, page)
        if self._offscreen is None:
            self._offscreen = OffscreenPageRenderer()
        return self._offscreen.render(self._metadata, page)
