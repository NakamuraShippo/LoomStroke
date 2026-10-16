"""Selection rendering policy helpers for vector text."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from PySide6.QtGui import QColor


@dataclass(frozen=True)
class TextSelectionRenderPolicy:
    selected_here: bool
    hide_source_text: bool
    show_transform_overlay: bool


@dataclass(frozen=True)
class HorizontalTextSelectionSpan:
    start: int
    end: int
    color: QColor | None


class SelectionRenderEngine:
    """Selection span and transform-overlay policies for vector text."""

    @staticmethod
    def horizontal_text_selection_span(
        selection: tuple[int, int] | None,
        *,
        cursor_color: QColor | None,
        fallback_color: QColor,
    ) -> HorizontalTextSelectionSpan:
        sel_start, sel_end = selection if selection else (-1, -1)
        if sel_start < 0 or sel_end <= sel_start:
            return HorizontalTextSelectionSpan(start=sel_start, end=sel_end, color=None)

        base_color = QColor(cursor_color) if cursor_color is not None else QColor(fallback_color)
        if base_color.alpha() <= 0:
            base_color.setAlpha(255)
        sel_color = QColor(base_color)
        sel_color.setAlpha(max(32, int(base_color.alpha() * 0.35)))
        return HorizontalTextSelectionSpan(start=sel_start, end=sel_end, color=sel_color)

    @staticmethod
    def text_selection_render_policy(
        *,
        selection: Mapping[str, Any] | None,
        object_id: Any,
        layer_id: str,
        vertical: bool,
        is_preview_object: bool,
    ) -> TextSelectionRenderPolicy:
        if not isinstance(selection, Mapping):
            return TextSelectionRenderPolicy(
                selected_here=False,
                hide_source_text=False,
                show_transform_overlay=True,
            )
        selected_here = bool(
            str(selection.get("id")) == str(object_id)
            and (
                selection.get("layer_id") is None or str(selection.get("layer_id")) == str(layer_id)
            )
        )
        selection_owner = str(selection.get("owner"))
        hide_source_text = bool(
            selected_here
            and (not is_preview_object)
            and (
                selection.get("hide_source_text")
                or (
                    vertical
                    and selection_owner in {"text_tool_focus", "text_tool_inline"}
                    and bool(selection.get("preview", True))
                )
            )
        )
        return TextSelectionRenderPolicy(
            selected_here=selected_here,
            hide_source_text=hide_source_text,
            show_transform_overlay=bool(selection.get("show_transform_overlay", True)),
        )

    @staticmethod
    def coerce_overlay_bounds(
        raw_bounds: Any,
        fallback: tuple[float, float, float, float] | None = None,
    ) -> tuple[float, float, float, float] | None:
        if isinstance(raw_bounds, (tuple, list)) and len(raw_bounds) == 4:
            try:
                return (
                    float(raw_bounds[0]),
                    float(raw_bounds[1]),
                    float(raw_bounds[2]),
                    float(raw_bounds[3]),
                )
            except (TypeError, ValueError):
                return fallback
        return fallback

    @staticmethod
    def text_object_transform_overlay_bounds(
        *,
        selection_policy: TextSelectionRenderPolicy,
        selection: Mapping[str, Any] | None,
        fallback_bounds: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float] | None:
        if not (selection_policy.selected_here and selection):
            return None
        if not selection_policy.show_transform_overlay:
            return None
        return VectorTextRenderService.coerce_overlay_bounds(
            selection.get("transform_bounds"),
            fallback=fallback_bounds,
        )


VectorTextRenderService = SelectionRenderEngine

__all__ = (
    "HorizontalTextSelectionSpan",
    "SelectionRenderEngine",
    "TextSelectionRenderPolicy",
)
