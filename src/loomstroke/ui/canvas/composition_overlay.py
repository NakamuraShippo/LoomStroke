"""View-only composition guides; never part of render snapshots or exports."""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from ...core.composition_geometry import KINDS, composition_lines
from ...core.guide_transform import transform_lines
from .guide_reference import guide_reference_rect


@lru_cache(maxsize=128)
def _lines(rect: tuple, kind: str, percent: float, offset: tuple, angle: float = 0) -> tuple:
    return transform_lines(
        composition_lines(rect, (kind,), percent),
        (rect[0] + rect[2] / 2, rect[1] + rect[3] / 2),
        angle,
        offset,
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
    reference = read("view/composition_overlay_reference", "canvas")
    rect = guide_reference_rect(widget, reference)
    if rect is None:
        return {}
    try:
        percent = float(read("view/composition_overlay_safe_percent", 10.0))
        angle = float(read("view/composition_overlay_rotation", 0.0))
        if not math.isfinite(angle):
            raise ValueError("Non-finite rotation")
    except (TypeError, ValueError):
        return {}
    offsets = read("view/composition_overlay_offsets", {})
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
        groups[kind] = _lines(tuple(rect), kind, percent, (dx, dy), angle)
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
