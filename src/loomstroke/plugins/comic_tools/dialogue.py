"""Dialogue index: document-wide balloon list (InDesign-style 台詞一覧).

Every row knows its page/panel/balloon ids, speaker, text preview, writing
mode, and status, so the UI can jump from the list to the exact balloon.
Rows are built from page snapshots (active page persisted first — the stored
snapshot of the active page lags behind the live canvas otherwise).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .model import (
    BALLOON_META_KEY,
    BALLOON_TEXT_META_KEY,
    BalloonRecord,
    balloon_status,
)


@dataclass(frozen=True)
class BalloonListRow:
    balloon_id: str
    page_id: str
    page_name: str
    page_index: int
    panel_id: str
    speaker_id: str
    speaker_name: str
    text: str
    text_preview: str
    vertical: bool
    status: str


def _object_meta(obj: Any) -> Mapping[str, Any]:
    meta = obj.get("meta") if isinstance(obj, Mapping) else getattr(obj, "meta", None)
    return meta if isinstance(meta, Mapping) else {}


def _iter_vector_objects(layers: Any):
    stack = list(layers or [])
    while stack:
        layer = stack.pop()
        if isinstance(layer, Mapping):
            children = layer.get("children") or []
            objects = layer.get("vector_objects") or []
        else:
            children = getattr(layer, "children", None) or []
            objects = getattr(layer, "vector_objects", None) or []
        stack.extend(children)
        yield from objects


def _texts_by_balloon(layers: Any) -> dict[str, tuple[str, bool]]:
    texts: dict[str, tuple[str, bool]] = {}
    for obj in _iter_vector_objects(layers):
        meta = _object_meta(obj)
        link = meta.get(BALLOON_TEXT_META_KEY)
        if not isinstance(link, Mapping):
            continue
        balloon_id = str(link.get("balloon_id") or "")
        if not balloon_id:
            continue
        payload = meta.get("text_tool")
        text = ""
        vertical = True
        if isinstance(payload, Mapping):
            document = payload.get("document")
            if isinstance(document, Mapping):
                text = str(document.get("text") or "")
            style_map = payload.get("style_map")
            if isinstance(style_map, Mapping):
                default = style_map.get("default")
                if isinstance(default, Mapping):
                    vertical = bool(default.get("vertical", True))
        texts[balloon_id] = (text, vertical)
    return texts


def rows_for_page(
    layers: Any,
    *,
    page_id: str,
    page_name: str = "",
    page_index: int = 0,
) -> list[BalloonListRow]:
    texts = _texts_by_balloon(layers)
    rows: list[BalloonListRow] = []
    for obj in _iter_vector_objects(layers):
        record = BalloonRecord.from_meta(_object_meta(obj).get(BALLOON_META_KEY))
        if record is None:
            continue
        text, vertical = texts.get(record.balloon_id, ("", True))
        preview = " ".join(text.split())
        if len(preview) > 40:
            preview = preview[:39] + "…"
        rows.append(
            BalloonListRow(
                balloon_id=record.balloon_id,
                page_id=page_id,
                page_name=page_name,
                page_index=page_index,
                panel_id=record.panel_id,
                speaker_id=record.speaker_id,
                speaker_name=record.speaker_name,
                text=text,
                text_preview=preview,
                vertical=vertical,
                status=balloon_status(text),
            )
        )
    return rows


def build_dialogue_rows(pages_service: Any) -> list[BalloonListRow]:
    """Document-wide balloon rows, in page order.

    ``pages_service`` is the runtime page service (duck-typed:
    ``persist_active_page`` / ``list_pages`` / ``get_page_snapshot``).
    """

    persist = getattr(pages_service, "persist_active_page", None)
    if callable(persist):
        persist()
    rows: list[BalloonListRow] = []
    list_pages = getattr(pages_service, "list_pages", None)
    get_snapshot = getattr(pages_service, "get_page_snapshot", None)
    if not callable(list_pages) or not callable(get_snapshot):
        return rows
    for index, summary in enumerate(list_pages()):
        page_id = str(getattr(summary, "id", "") or "")
        snapshot = get_snapshot(page_id) if page_id else None
        if snapshot is None:
            continue
        rows.extend(
            rows_for_page(
                getattr(snapshot, "layers", None) or [],
                page_id=page_id,
                page_name=str(getattr(summary, "name", "") or ""),
                page_index=index,
            )
        )
    return rows


__all__ = ["BalloonListRow", "build_dialogue_rows", "rows_for_page"]
