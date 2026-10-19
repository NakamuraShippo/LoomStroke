"""Foreground/background colour pair backing the tool palette swatch.

The foreground colour is the host's active colour (changed through
``HostAPI.set_active_color`` so the existing command / event path is kept).
The background colour is UI-level state persisted in settings, stored as a
full descriptor so native-CMYK ink metadata survives a swap.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

from loomstroke.core.color_state import ColorDescriptor, ColorStateSnapshot

logger = logging.getLogger(__name__)

BACKGROUND_COLOR_SETTING = "color/background"
DEFAULT_FOREGROUND = ColorDescriptor((0.0, 0.0, 0.0, 1.0))
DEFAULT_BACKGROUND = ColorDescriptor((1.0, 1.0, 1.0, 1.0))
_SOURCE = "tool_palette_swatch"

ColorPicker = Callable[[QColor, QWidget | None], QColor | None]


def descriptor_to_qcolor(color: ColorDescriptor) -> QColor:
    r, g, b, a = color.clamp().rgba
    return QColor.fromRgbF(r, g, b, a)


def _descriptor_from_qcolor(color: QColor, *, blend_mode: str = "normal") -> ColorDescriptor:
    return ColorDescriptor(
        (color.redF(), color.greenF(), color.blueF(), color.alphaF()),
        blend_mode=blend_mode,
    )


def _descriptor_to_setting(color: ColorDescriptor) -> dict[str, Any]:
    return {
        "rgba": [float(value) for value in color.rgba],
        "meta": dict(color.meta) if color.meta else None,
    }


def _descriptor_from_setting(value: Any) -> ColorDescriptor | None:
    if not isinstance(value, Mapping):
        return None
    try:
        rgba = tuple(float(component) for component in value.get("rgba", ()))
    except (TypeError, ValueError):
        return None
    if len(rgba) == 3:
        rgba = (*rgba, 1.0)
    if len(rgba) != 4:
        return None
    meta = value.get("meta")
    return ColorDescriptor(rgba, meta=meta if isinstance(meta, Mapping) else None).clamp()


class ForegroundBackgroundColors(QObject):
    """Own the background colour and the swap / reset operations."""

    colors_changed = Signal()

    def __init__(self, host_api: Any, settings: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._host = host_api
        self._settings = settings
        self._background = self._load_background()
        subscribe = getattr(host_api, "subscribe_color_changed", None)
        if callable(subscribe):
            try:
                # HostAPI keeps a strong reference to the wrapping handler.
                subscribe(self._on_active_color_changed)
            except Exception:
                logger.debug("Color change subscription unavailable", exc_info=True)

    def foreground(self) -> ColorDescriptor:
        getter = getattr(self._host, "get_active_color", None)
        if callable(getter):
            try:
                color = getter()
            except Exception:
                logger.debug("Active color unavailable", exc_info=True)
            else:
                if isinstance(color, ColorDescriptor):
                    return color
        return DEFAULT_FOREGROUND

    def background(self) -> ColorDescriptor:
        return self._background

    def set_foreground(self, color: ColorDescriptor) -> None:
        setter = getattr(self._host, "set_active_color", None)
        if not callable(setter):
            return
        current = self.foreground()
        setter(
            ColorDescriptor(color.rgba, blend_mode=current.blend_mode, meta=color.meta),
            source=_SOURCE,
        )
        self.colors_changed.emit()

    def set_background(self, color: ColorDescriptor) -> None:
        self._background = ColorDescriptor(color.rgba, meta=color.meta).clamp()
        self._persist_background()
        self.colors_changed.emit()

    def swap(self) -> None:
        foreground = self.foreground()
        background = self._background
        self._background = ColorDescriptor(foreground.rgba, meta=foreground.meta).clamp()
        self._persist_background()
        self.set_foreground(background)

    def reset_defaults(self) -> None:
        self._background = DEFAULT_BACKGROUND
        self._persist_background()
        self.set_foreground(DEFAULT_FOREGROUND)

    def _on_active_color_changed(self, _snapshot: ColorStateSnapshot) -> None:
        self.colors_changed.emit()

    def _load_background(self) -> ColorDescriptor:
        getter = getattr(self._settings, "get", None)
        if not callable(getter):
            return DEFAULT_BACKGROUND
        try:
            stored = getter(BACKGROUND_COLOR_SETTING, None)
        except Exception:
            logger.debug("Failed to read background color", exc_info=True)
            return DEFAULT_BACKGROUND
        return _descriptor_from_setting(stored) or DEFAULT_BACKGROUND

    def _persist_background(self) -> None:
        setter = getattr(self._settings, "set", None)
        if not callable(setter):
            return
        try:
            setter(BACKGROUND_COLOR_SETTING, _descriptor_to_setting(self._background))
        except Exception:
            logger.debug("Failed to persist background color", exc_info=True)


def _default_picker(initial: QColor, parent: QWidget | None) -> QColor | None:
    from loomstroke.ui.widgets.color_picker_popup import ColorPickerPopup  # noqa: PLC0415

    return ColorPickerPopup.pick_color(initial, parent)


def bind_color_swatch(
    swatch: Any,
    colors: ForegroundBackgroundColors,
    *,
    picker: ColorPicker = _default_picker,
) -> None:
    """Connect a :class:`ForegroundBackgroundSwatch` to the colour pair."""

    def _refresh() -> None:
        swatch.set_colors(
            descriptor_to_qcolor(colors.foreground()),
            descriptor_to_qcolor(colors.background()),
        )

    def _pick_foreground() -> None:
        chosen = picker(descriptor_to_qcolor(colors.foreground()), swatch.window())
        if chosen is not None and chosen.isValid():
            colors.set_foreground(_descriptor_from_qcolor(chosen))

    def _pick_background() -> None:
        chosen = picker(descriptor_to_qcolor(colors.background()), swatch.window())
        if chosen is not None and chosen.isValid():
            colors.set_background(_descriptor_from_qcolor(chosen))

    swatch.foreground_requested.connect(_pick_foreground)
    swatch.background_requested.connect(_pick_background)
    swatch.swap_requested.connect(colors.swap)
    swatch.reset_requested.connect(colors.reset_defaults)
    colors.colors_changed.connect(_refresh)
    _refresh()


__all__ = [
    "BACKGROUND_COLOR_SETTING",
    "DEFAULT_BACKGROUND",
    "DEFAULT_FOREGROUND",
    "ForegroundBackgroundColors",
    "bind_color_swatch",
    "descriptor_to_qcolor",
]
