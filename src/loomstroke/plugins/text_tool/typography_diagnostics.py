"""Read-only layout diagnostics cached independently of cursor/selection changes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from loomstroke.core.japanese_text.typography import has_typography
from loomstroke.core.japanese_text.typography_layout import build_japanese_layout

from .common import extract_text_payload


class TextTypographyDiagnostics:
    def __init__(self, tool: Any) -> None:
        self._tool = tool
        self._payload: dict[str, Any] | None = None
        self._codes: list[str] = []

    def current(self) -> list[str]:
        tool = self._tool
        state = tool._inline_state
        if state is not None:
            canvas = getattr(tool.host_api, "canvas", None)
            preview = getattr(canvas, "_vector_preview", None)
            payload = extract_text_payload(preview or state.object_ref)
        else:
            hit = tool._selected_text_hit()
            payload = hit.payload if hit is not None else None
        if payload is not None:
            payload = dict(payload)
            payload.pop("inline_preview", None)
            payload.pop("updated_at", None)
        if payload != self._payload:
            codes = (
                list(build_japanese_layout(payload).diagnostics) if has_typography(payload) else []
            )
            self._payload = deepcopy(payload)
            self._codes = codes
        return list(self._codes)
