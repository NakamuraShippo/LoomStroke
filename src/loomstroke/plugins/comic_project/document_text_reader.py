"""Read text objects of document pages for link checks (CT-17 §6 link resolver input).

Main-thread adapter over the PageService: page ids and ``{object_id:
content}`` per page, read from page snapshots (hydrated on demand and
handed back to the CT-08 budget).  The text is only compared, never
logged or persisted.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from loomstroke.core.vector_text_layout import TextLayoutCalculator

logger = logging.getLogger(__name__)

__all__ = ["ComicDocumentTextReader"]


class ComicDocumentTextReader:
    def __init__(self, page_service: Any) -> None:
        self._pages = page_service

    def page_ids(self) -> tuple[str, ...]:
        list_pages = getattr(self._pages, "list_pages", None)
        if not callable(list_pages):
            return ()
        try:
            return tuple(str(summary.id) for summary in (list_pages() or []))
        except Exception:
            return ()

    def page_size(self, page_id: str, fallback: tuple[int, int]) -> tuple[int, int]:
        list_pages = getattr(self._pages, "list_pages", None)
        if callable(list_pages):
            for summary in list_pages() or []:
                if str(summary.id) == page_id:
                    canvas = getattr(summary, "canvas", None)
                    width = int(getattr(canvas, "width", 0) or 0)
                    height = int(getattr(canvas, "height", 0) or 0)
                    if width > 0 and height > 0:
                        return (width, height)
        return fallback

    def texts_for_page(self, page_id: str) -> dict[str, str]:
        snapshot = getattr(self._pages, "get_page_snapshot", None)
        if not callable(snapshot):
            return {}
        texts: dict[str, str] = {}
        try:
            page = snapshot(page_id)
            if page is None:
                return {}
            for layer in getattr(page, "layers", None) or []:
                self._collect(layer, texts)
        except Exception:
            logger.debug("comic project: page text read failed for %s", page_id, exc_info=True)
        finally:
            evict = getattr(self._pages, "evict_pages_to_budget", None)
            if callable(evict):
                evict()
        return texts

    def texts_by_page(self, page_ids: Iterable[str]) -> dict[str, dict[str, str]]:
        return {page_id: self.texts_for_page(page_id) for page_id in page_ids}

    def _collect(self, layer: Any, texts: dict[str, str]) -> None:
        for vec in getattr(layer, "vector_objects", None) or []:
            payload = TextLayoutCalculator.extract_text_payload(vec)
            if payload is None:
                continue
            object_id = str(getattr(vec, "id", "") or "")
            if object_id:
                texts[object_id] = TextLayoutCalculator.text_content_from_payload(payload)
        for child in getattr(layer, "children", None) or []:
            self._collect(child, texts)
