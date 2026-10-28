"""View-only composition guides; never part of render snapshots or exports."""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from ...core.composition_geometry import KINDS, composition_lines, reference_rect


@lru_cache(maxsize=128)
def _lines(rect: tuple, kind: str, percent: float, offset: tuple) -> tuple:
    return tuple(
        tuple((x + offset[0], y + offset[1]) for x, y in line)
        for line in composition_lines(rect, (kind,), percent)
    )


def composition_groups(widget: Any, *, preview: bool = False) -> dict[str, tuple]:
    host = getattr(widget, "_host_api", None)
    settings = getattr(host, "settings", None)
    if settings is None:
        return {}
    session = getattr(widget, "_composition_dialog_preview", None) if preview else None
    values = session.current_values() if session is not None else None

    def read(key: str, default: Any) -> Any:
        return values[key] if values is not None and key in values else settings.get(key, default)

    if not read("view/guides_visible", True):
        return {}
    kinds = read("view/composition_overlay_kinds", [])
    if not isinstance(kinds, (list, tuple)) or not kinds:
        return {}
    pages = getattr(host, "pages", None)
    guides = []
    reference = read("view/composition_overlay_reference", "canvas")
    if pages is not None and reference == "trim":
        view = pages.get_page_view_state(pages.get_active_page_id())
        guides = view.get("guides", []) if isinstance(view, dict) else []
    if not isinstance(guides, (list, tuple)):
        guides = []
    bounds = getattr(getattr(host, "canvas", None), "_selection_bounds", None)
    selection = (
        None
        if bounds is None or bounds.isEmpty()
        else (bounds.x(), bounds.y(), bounds.width(), bounds.height())
    )
    rect = reference_rect(
        (widget.state.width, widget.state.height),
        reference,
        guides=(g for g in guides if isinstance(g, dict)),
        selection=selection,
    )
    if rect is None:
        return {}
    try:
        percent = float(read("view/composition_overlay_safe_percent", 10.0))
    except (TypeError, ValueError):
        return {}
    offsets = settings.get("view/composition_overlay_offsets", {})
    offsets = offsets if isinstance(offsets, dict) else {}
    interaction = getattr(widget, "_composition_interaction", None)
    groups = {}
    for kind in KINDS:
        if kind not in kinds:
            continue
        offset = offsets.get(kind, (0.0, 0.0))
        try:
            dx, dy = map(float, offset)
        except (TypeError, ValueError):
            dx, dy = 0.0, 0.0
        if not math.isfinite(dx) or not math.isfinite(dy):
            dx, dy = 0.0, 0.0
        if interaction is not None:
            dx, dy = interaction.preview_offset(kind, (dx, dy))
        groups[kind] = _lines(tuple(rect), kind, percent, (dx, dy))
    return groups


def draw_composition_overlay(painter: QPainter, widget: Any) -> None:
    groups = composition_groups(widget, preview=True)
    if not groups:
        return
    painter.save()
    try:
        painter.setClipRect(
            QRectF(0, 0, widget.state.width, widget.state.height), Qt.ClipOperation.IntersectClip
        )
        pen = QPen(QColor(240, 190, 60, 180), 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        selected = getattr(getattr(widget, "_composition_interaction", None), "selected", None)
        for kind, lines in groups.items():
            pen.setColor(
                QColor(255, 255, 120, 255) if kind == selected else QColor(240, 190, 60, 180)
            )
            painter.setPen(pen)
            for start, end in lines:
                painter.drawLine(QPointF(*start), QPointF(*end))
    finally:
        painter.restore()
