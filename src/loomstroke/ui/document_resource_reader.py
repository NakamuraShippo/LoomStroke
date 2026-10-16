"""Collects ``(page_id, layer_id, layer_name, metadata)`` for every layer of the document.

Active page: live canvas layers.  Other pages: their stored snapshots (no
hydration), mirroring ``DocumentTextPayloadReader`` (CT-25).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from loomstroke.core.linked_assets.resource_consumer_index import (
    LayerEntry,
    ResourceConsumerIndex,
)

__all__ = ["DocumentResourceReader"]


class DocumentResourceReader:
    def __init__(self, host: Any) -> None:
        self._host = host

    def active_page_id(self) -> str:
        pages = getattr(self._host, "pages", None)
        getter = getattr(pages, "get_active_page_id", None)
        try:
            value = getter() if callable(getter) else None
        except Exception:
            value = None
        return value if isinstance(value, str) else ""

    def entries(self) -> list[LayerEntry]:
        active = self.active_page_id()
        entries: list[LayerEntry] = [
            (
                active,
                str(layer.id),
                str(getattr(layer, "name", "") or ""),
                getattr(layer, "metadata", None),
            )
            for layer in self._live_layers()
        ]
        pages = getattr(self._host, "pages", None)
        if pages is None:
            return entries
        try:
            summaries = list(pages.list_pages())
        except Exception:
            summaries = []
        for summary in summaries:
            page_id = str(getattr(summary, "id", "") or "")
            if not page_id or page_id == active:
                continue
            snapshot = self._snapshot(pages, page_id)
            if snapshot is not None:
                self._walk(page_id, getattr(snapshot, "layers", ()) or (), entries)
        return entries

    def index(self) -> ResourceConsumerIndex:
        return ResourceConsumerIndex.build(self.entries())

    # -- helpers -----------------------------------------------------------------------

    def _live_layers(self) -> list[Any]:
        canvas = getattr(self._host, "canvas", None)
        flatten = getattr(canvas, "_flatten_layers", None)
        if callable(flatten):
            try:
                return list(flatten())
            except Exception:
                return []
        layers = getattr(canvas, "_layers", None)
        return list(layers) if isinstance(layers, list) else []

    @staticmethod
    def _snapshot(pages: Any, page_id: str) -> Any | None:
        getter = getattr(pages, "get_page_snapshot", None)
        if not callable(getter):
            return None
        try:
            return getter(page_id)
        except Exception:
            return None

    @classmethod
    def _walk(cls, page_id: str, layers: Iterable[Any], out: list[LayerEntry]) -> None:
        for layer in layers:
            out.append(
                (
                    page_id,
                    str(getattr(layer, "id", "") or ""),
                    str(getattr(layer, "name", "") or ""),
                    getattr(layer, "metadata", None),
                )
            )
            children = getattr(layer, "children", None)
            if children:
                cls._walk(page_id, children, out)
