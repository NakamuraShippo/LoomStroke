"""Selection-aware text properties and their read-only UI synchronization."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, replace
from typing import Any

from loomstroke.core.japanese_text.character_geometry import geometry_values
from loomstroke.core.japanese_text.frame_flow import flow_values, payload_flow
from loomstroke.core.japanese_text.typography import normalize_typography

from .character_style_controller import selection_color, selection_font_values
from .common import TextToolSettings, extract_text_payload
from .typography_diagnostics import TextTypographyDiagnostics

logger = logging.getLogger(__name__)
_STYLE_KEYS = {
    "font_family",
    "font_style",
    "font_size_pt",
    "color_rgba",
    "vertical",
    "tracking",
    "rotate_half_width",
}
_PARAGRAPH_KEYS = {"align", "line_height", "vertical_flow"}


def text_settings_dict(settings: TextToolSettings) -> dict[str, Any]:
    values = asdict(settings)
    values["color_rgba"] = list(settings.color_rgba)
    return values


def replace_text_property(obj: Any, key: str, value: Any) -> Any:
    """Change one property while preserving other styles and extension metadata."""
    original = extract_text_payload(obj)
    if original is None:
        return obj
    payload = deepcopy(original)
    if key not in {"rotate_half_width", "typography", "text_flow"}:
        payload[key] = deepcopy(value)
    if key == "typography":
        layout = payload.get("layout")
        layout = deepcopy(layout) if isinstance(layout, Mapping) else {}
        layout["japanese"] = normalize_typography(value)
        payload["layout"] = layout
    if key == "text_flow":
        payload.setdefault("layout", {})["text_flow"] = flow_values(value)
        payload["word_wrap"] = True
        _replace_wrap_properties(payload, True)
    if key in _STYLE_KEYS:
        styles = payload.get("style_map")
        if isinstance(styles, dict) and isinstance(styles.get("default"), dict):
            styles["default"][key] = deepcopy(value)
        elif key == "rotate_half_width":
            payload[key] = value
    if key in _PARAGRAPH_KEYS:
        _replace_paragraph_property(payload, key, value)
    layout = payload.get("layout")
    if key.startswith("padding_") and isinstance(layout, dict):
        layout[key] = value
    if key == "word_wrap":
        _replace_wrap_properties(payload, value)
    if payload == original:
        return obj
    payload["updated_at"] = time.time()
    meta = deepcopy(obj.meta)
    meta["text_tool"] = payload
    style = obj.style
    if key == "color_rgba":
        style = replace(style, stroke_color=tuple(value[:3]))
    return replace(obj, meta=meta, style=style)


def _replace_paragraph_property(payload: dict[str, Any], key: str, value: Any) -> None:
    document = payload.get("document")
    paragraphs = document.get("paragraphs") if isinstance(document, dict) else None
    if isinstance(paragraphs, list):
        for paragraph in paragraphs:
            if isinstance(paragraph, dict):
                paragraph[key] = value


def _replace_wrap_properties(payload: dict[str, Any], word_wrap: bool) -> None:
    payload["text_mode"] = "area" if word_wrap else "point"
    payload["auto_size"] = not word_wrap
    frame = payload.get("frame")
    if isinstance(frame, dict):
        frame["auto_size"] = not word_wrap
    layout = payload.get("layout")
    if not isinstance(layout, dict):
        return
    if not word_wrap:
        layout["wrap_mode"] = "none"
    elif isinstance(payload.get("shape_frame"), Mapping):
        layout["wrap_mode"] = "shape"
    elif layout.get("wrap_mode") not in {"word", "char", "shape"}:
        layout["wrap_mode"] = "word"
    layout["overflow_mode"] = "clip" if word_wrap else "expand"


class TextPropertiesController:
    """Mirror selection/history into tool UI without overwriting creation defaults."""

    def __init__(self, tool: Any) -> None:
        self._tool = tool
        self._signal: Any = None
        self._syncing = False
        self._last_values: dict[str, Any] | None = None
        self._diagnostics = TextTypographyDiagnostics(tool)

    def current_settings(self) -> TextToolSettings:
        tool = self._tool
        if tool._inline_state is not None:
            return tool._inline_effective_settings()
        hit = tool._selected_text_hit()
        return tool._settings_from_payload(hit.payload) if hit is not None else tool._settings()

    def current_values(self) -> dict[str, Any]:
        values = text_settings_dict(self.current_settings())
        values.update(geometry_values({}))
        color, selected = selection_color(self._tool, values["color_rgba"])
        values["color_rgba"] = list(color)
        values["color_range_selected"] = selected
        values.update(selection_font_values(self._tool))
        values["annotation_selection"] = self._tool._annotations.selection_state()
        values["typography_diagnostics"] = self._diagnostics.current()
        state = self._tool._inline_state
        hit = self._tool._selected_text_hit()
        obj = state.object_ref if state is not None else (hit.obj if hit else None)
        values["text_flow"] = payload_flow(extract_text_payload(obj) or {})
        payload = extract_text_payload(obj) or {}
        values["rectangular_text_frame"] = not bool(payload.get("shape_frame"))
        values["linked_text_frame"] = bool(payload.get("linked_story"))
        values["text_frame_selected"] = obj is not None or state is not None
        from .link_order_dialog import selected_frames  # noqa: PLC0415

        values["selected_frame_count"] = len(selected_frames(self._tool))
        values["text_frame_selected"] |= values["selected_frame_count"] > 1
        from .frame_flow_controller import link_targets  # noqa: PLC0415

        values["link_targets"] = link_targets(self._tool, obj)
        return values

    def connect(self) -> None:
        self.disconnect()
        canvas = getattr(self._tool.host_api, "canvas", None)
        signal = getattr(canvas, "composited_changed", None)
        if hasattr(signal, "connect"):
            signal.connect(self.sync)
            self._signal = signal

    def disconnect(self) -> None:
        if self._signal is not None:
            try:
                self._signal.disconnect(self.sync)
            except (RuntimeError, TypeError):
                logger.debug("Text property signal already disconnected", exc_info=True)
        self._signal = None
        self._last_values = None

    def sync(self, *_args: Any, force: bool = False) -> None:
        if self._syncing:
            return
        tool = self._tool
        host = tool.host_api
        manager = getattr(host, "_tool_manager", None) or getattr(host, "tool_manager", None)
        sync = getattr(manager, "sync_tool_setting", None)
        if not callable(sync):
            return
        self._syncing = True
        try:
            values = self.current_values()
            if not force and values == self._last_values:
                return
            self._last_values = deepcopy(values)
            for tool_id in tool._iter_registered_text_tool_ids(manager):
                for key, value in values.items():
                    sync(tool_id, key, value)
        finally:
            self._syncing = False
