"""Scale the pixel-space parts of ``meta["text_tool"]`` (CT-13 R3, spec §10.3).

Frame, padding and tracking are canvas pixels and follow the axis scale.
Font sizes follow the uniform (geometric-mean) scale so text keeps its
proportion to the artwork — the layout engine feeds ``font_size_pt`` to
``QFont.setPointSizeF`` without consulting the document DPI, so the point
size *is* the pixel-proportional size.  Everything else (alignment,
vertical flow, shape-wrap paths which are frame-normalized, mask PNGs that
are rescaled at draw time) is left untouched, and unknown keys survive.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


class TextMetaScaler:
    """Return a scaled copy of a text vector object's metadata."""

    META_KEY = "text_tool"
    _X_KEYS = ("x", "width", "padding_left", "padding_right")
    _Y_KEYS = ("y", "height", "padding_top", "padding_bottom")
    _FRAME_X_KEYS = ("x", "width")
    _FRAME_Y_KEYS = ("y", "height")

    def __init__(self, *, scale_font_size: bool = True) -> None:
        self._scale_font_size = bool(scale_font_size)

    def scale(self, meta: Mapping[str, Any], sx: float, sy: float) -> dict[str, Any] | None:
        """Scaled copy of *meta*, or ``None`` when it carries no text payload."""
        text = meta.get(self.META_KEY) if isinstance(meta, Mapping) else None
        if not isinstance(text, Mapping):
            return None
        uniform = self.uniform_factor(sx, sy)
        new_text: dict[str, Any] = dict(text)

        frame = text.get("frame")
        if isinstance(frame, Mapping):
            new_frame = dict(frame)
            self._scale_keys(new_frame, self._FRAME_X_KEYS, sx)
            self._scale_keys(new_frame, self._FRAME_Y_KEYS, sy)
            new_text["frame"] = new_frame

        self._scale_keys(new_text, self._X_KEYS, sx)
        self._scale_keys(new_text, self._Y_KEYS, sy)

        layout = text.get("layout")
        if isinstance(layout, Mapping):
            new_layout = dict(layout)
            self._scale_keys(new_layout, ("padding_left", "padding_right"), sx)
            self._scale_keys(new_layout, ("padding_top", "padding_bottom"), sy)
            new_text["layout"] = new_layout

        self._scale_keys(new_text, ("tracking",), uniform)
        runs = text.get("tracking_runs")
        if isinstance(runs, list):
            new_text["tracking_runs"] = [self._scale_run(run, uniform) for run in runs]

        if self._scale_font_size:
            self._scale_keys(new_text, ("font_size_pt",), uniform)
            style_map = text.get("style_map")
            if isinstance(style_map, Mapping):
                new_styles: dict[str, Any] = {}
                for style_id, style in style_map.items():
                    if isinstance(style, Mapping):
                        new_style = dict(style)
                        self._scale_keys(new_style, ("font_size_pt",), uniform)
                        new_styles[style_id] = new_style
                    else:
                        new_styles[style_id] = style
                new_text["style_map"] = new_styles

        new_meta = dict(meta)
        new_meta[self.META_KEY] = new_text
        return new_meta

    @staticmethod
    def uniform_factor(sx: float, sy: float) -> float:
        return math.sqrt(abs(float(sx) * float(sy)))

    @classmethod
    def _scale_keys(cls, payload: dict[str, Any], keys: tuple[str, ...], factor: float) -> None:
        for key in keys:
            if key in payload:
                payload[key] = cls._scaled(payload[key], factor)

    @staticmethod
    def _scale_run(run: Any, factor: float) -> Any:
        if not isinstance(run, Mapping) or "value" not in run:
            return run
        new_run = dict(run)
        new_run["value"] = TextMetaScaler._scaled(new_run["value"], factor)
        return new_run

    @staticmethod
    def _scaled(value: Any, factor: float) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value
        return float(value) * float(factor)
