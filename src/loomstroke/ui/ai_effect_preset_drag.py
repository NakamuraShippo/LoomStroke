"""Shared MIME helpers for dragging AI Effect presets between UI panels."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QByteArray, QMimeData

AI_EFFECT_PRESET_MIME = "application/x-loomstroke-ai-effect-preset+json"


def make_ai_effect_preset_mime_data(preset_id: str) -> QMimeData:
    """Build MIME data for an AI Effect preset drag operation."""
    mime_data = QMimeData()
    preset_id_text = str(preset_id or "").strip()
    if not preset_id_text:
        return mime_data
    payload = json.dumps(
        {"version": 1, "preset_id": preset_id_text},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    mime_data.setData(AI_EFFECT_PRESET_MIME, QByteArray(payload))
    return mime_data


def ai_effect_preset_id_from_mime(mime_data: Any) -> str | None:
    """Return a preset id from AI Effect preset MIME data, or ``None``."""
    preset_id: str | None = None
    has_preset_format = False
    has_format = getattr(mime_data, "hasFormat", None)
    if callable(has_format):
        try:
            has_preset_format = bool(has_format(AI_EFFECT_PRESET_MIME))
        except (RuntimeError, TypeError):
            has_preset_format = False

    data_getter = getattr(mime_data, "data", None)
    if has_preset_format and callable(data_getter):
        payload: Any = None
        try:
            raw_data = data_getter(AI_EFFECT_PRESET_MIME)
            raw_bytes = bytes(raw_data)
            payload = json.loads(raw_bytes.decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("version") == 1:
            raw_preset_id = payload.get("preset_id")
            if isinstance(raw_preset_id, str):
                preset_id = raw_preset_id.strip() or None
    return preset_id
