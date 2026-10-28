"""Calculate page-owned canvas changes without promoting them to document defaults."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtGui import QColor

from loomstroke.host_api import CanvasSpec as HostCanvasSpec
from loomstroke.io.layer_io import CanvasSpec as PersistCanvasSpec


def merge_page_canvas(
    stored: PersistCanvasSpec,
    live: HostCanvasSpec | None,
    *,
    simple_canvas: bool,
) -> PersistCanvasSpec:
    """Merge only changes distinguishable from the canvas's ordinary apply loss.

    SimpleCanvas applies integer dimensions and only an integer first-axis DPI.
    An unchanged live value must therefore preserve the original stored value.
    LegacyCanvas does not apply DPI at all, so its DPI cannot prove an edit.
    """
    if live is None:
        return stored
    expected_width, expected_height = stored.width, stored.height
    expected_background = stored.background_color
    dpi = stored.dpi
    if simple_canvas:
        expected_width = max(1, int(stored.width))
        expected_height = max(1, int(stored.height))
        applied_dpi = float(max(1, int(stored.dpi[0])))
        if live.dpi != (applied_dpi, applied_dpi):
            dpi = live.dpi
        expected_background = QColor(*stored.background_color).getRgb()
    return replace(
        stored,
        width=stored.width if live.width == expected_width else live.width,
        height=stored.height if live.height == expected_height else live.height,
        dpi=dpi,
        background_color=(
            stored.background_color
            if live.background_color == expected_background
            else live.background_color
        ),
    )


def current_page_canvas_spec(
    stored: PersistCanvasSpec,
    live: HostCanvasSpec | None,
    *,
    simple_canvas: bool,
) -> HostCanvasSpec:
    current = merge_page_canvas(stored, live, simple_canvas=simple_canvas)
    return HostCanvasSpec(
        width=current.width,
        height=current.height,
        dpi=current.dpi,
        background_color=current.background_color,
    )


def inherited_page_canvas(
    canvas_mode: str,
    base_canvas: PersistCanvasSpec | None,
    resolved: HostCanvasSpec,
) -> PersistCanvasSpec | None:
    """Keep a fixed source's resolved spec local when adding an inherited page."""
    if canvas_mode != "inherit_active" or base_canvas is None:
        return None
    return PersistCanvasSpec(
        width=resolved.width,
        height=resolved.height,
        dpi=resolved.dpi,
        background_color=resolved.background_color,
    )
