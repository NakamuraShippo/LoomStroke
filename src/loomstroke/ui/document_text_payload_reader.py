"""Enumerate text objects across the document for font work (CT-25 F1).

The active page is read from the live canvas (``get_vector_objects`` gives
layer ids the commands need); other pages come from page snapshots
(hydrated on demand, evicted afterwards).  Yields ``(page_id, layer_id,
object_id, payload)`` plus, for the active page, the live objects so the
action can build replacement commands.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from ..core.vector_text_layout import TextLayoutCalculator

logger = logging.getLogger(__name__)

__all__ = ["DocumentTextPayloadReader"]


class DocumentTextPayloadReader:
    def __init__(self, host_api: Any) -> None:
        self._host = host_api

    def active_page_id(self) -> str | None:
        pages = getattr(self._host, "pages", None)
        getter = getattr(pages, "get_active_page_id", None)
        try:
            value = getter() if callable(getter) else None
        except Exception:
            value = None
        return str(value) if value else None

    def active_page_objects(self) -> list[tuple[str, Any]]:
        """``(layer_id, vector_object)`` for text objects on the live canvas."""
        canvas = getattr(self._host, "canvas", None)
        getter = getattr(canvas, "get_vector_objects", None)
        if not callable(getter):
            return []
        try:
            entries = getter(include_hidden=True)
        except TypeError:
            entries = getter()
        except Exception:
            return []
        found: list[tuple[str, Any]] = []
        for entry in entries or []:
            layer_id, obj = (entry[0], entry[1]) if isinstance(entry, tuple) else (None, entry)
            if TextLayoutCalculator.extract_text_payload(obj) is not None:
                found.append((str(layer_id or ""), obj))
        return found

    def entries(self) -> Iterator[tuple[str, str, str, dict[str, Any]]]:
        active = self.active_page_id() or "active"
        for layer_id, obj in self.active_page_objects():
            payload = TextLayoutCalculator.extract_text_payload(obj)
            if payload is not None:
                yield active, layer_id, str(getattr(obj, "id", "")), dict(payload)
        pages = getattr(self._host, "pages", None)
        list_pages = getattr(pages, "list_pages", None)
        snapshot = getattr(pages, "get_page_snapshot", None)
        if not callable(list_pages) or not callable(snapshot):
            return
        for summary in list_pages() or []:
            page_id = str(summary.id)
            if page_id == active:
                continue
            try:
                page = snapshot(page_id)
                for layer in getattr(page, "layers", None) or []:
                    yield from self._layer_entries(page_id, layer)
            except Exception:
                logger.debug("font inventory: page %s unreadable", page_id, exc_info=True)
            finally:
                evict = getattr(pages, "evict_pages_to_budget", None)
                if callable(evict):
                    evict()

    def _layer_entries(
        self, page_id: str, layer: Any
    ) -> Iterator[tuple[str, str, str, dict[str, Any]]]:
        layer_id = str(getattr(layer, "id", "") or "")
        for vec in getattr(layer, "vector_objects", None) or []:
            payload = TextLayoutCalculator.extract_text_payload(vec)
            if payload is not None:
                yield page_id, layer_id, str(getattr(vec, "id", "")), dict(payload)
        for child in getattr(layer, "children", None) or []:
            yield from self._layer_entries(page_id, child)
