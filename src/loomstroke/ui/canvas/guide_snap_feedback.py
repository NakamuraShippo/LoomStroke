"""Transient highlighting of the guide actually chosen by snapping."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from ...core.composition_geometry import Line
from .composition_overlay import composition_groups
from .guide_canvas_bridge import update_guide_overlay
from .rotated_guides import segment


class GuideSnapFeedback:
    def __init__(self, widget: Any) -> None:
        self.widget = widget
        self.guides: tuple[tuple[str, float | str], ...] = ()
        self.composition: tuple[str, int | None] | None = None
        self.context: Any = None
        self.composition_geometry: tuple = ()

    def _context(self) -> tuple:
        host = getattr(self.widget, "_host_api", None)
        pages = getattr(host, "pages", None)
        page = pages.get_active_page_id() if pages is not None else None
        tool = getattr(self.widget, "_active_tool_id", lambda: None)()
        return page, tool

    def set_targets(self, guides: tuple, composition: tuple | None = None) -> None:
        if not guides and composition is None:
            self.clear()
            return
        context = self._context()
        geometry = composition_groups(self.widget).get(composition[0], ()) if composition else ()
        if (guides, composition, context, geometry) == (
            self.guides,
            self.composition,
            self.context,
            self.composition_geometry,
        ):
            return
        self.guides, self.composition, self.context = guides, composition, context
        self.composition_geometry = geometry
        update_guide_overlay(self.widget)

    def clear(self, *, repaint: bool = True) -> None:
        changed = bool(self.guides or self.composition is not None)
        self.guides, self.composition, self.context = (), None, None
        self.composition_geometry = ()
        if changed and repaint:
            update_guide_overlay(self.widget)

    def lines(self) -> tuple[Line, ...]:
        if not self.guides and self.composition is None:
            return ()
        host = getattr(self.widget, "_host_api", None)
        settings = getattr(host, "settings", None)
        if (
            self.context != self._context()
            or settings is None
            or not settings.get("view/guides_visible", True)
        ):
            self.clear(repaint=False)
            return ()
        width, height = self.widget.state.width, self.widget.state.height
        for attribute in ("_layout_dialog_preview", "_composition_dialog_preview"):
            preview = getattr(self.widget, attribute, None)
            if preview is not None and preview.current_values() is not None:
                self.clear(repaint=False)
                return ()
        controller = getattr(self.widget, "_guide_controller", None)
        lines = self._regular_lines(controller, settings, width, height)
        if self.composition is not None:
            kind, index = self.composition
            group = composition_groups(self.widget).get(kind, ())
            if group != self.composition_geometry:
                self.clear(repaint=False)
                return ()
            if index is None:
                lines.extend(group)
            elif 0 <= index < len(group):
                lines.append(group[index])
        if not lines:
            self.clear(repaint=False)
        return tuple(lines)

    def _regular_lines(self, controller, settings, width, height):
        lines = []
        if controller is not None and controller.guides_visible:
            current = {
                (guide.orientation, guide.position)
                for guide in controller.guides()
                if not guide.rotation % 360
            }
            for axis, position in self.guides:
                if axis == "id":
                    guide = next((g for g in controller.guides() if g.id == position), None)
                    line = segment(guide, (width, height)) if guide is not None else None
                    if line is not None:
                        lines.append(line)
                    continue
                extent = width if axis == "vertical" else height
                edge = settings.get("view/canvas_edge_snap", True) and position in (0, extent)
                center = settings.get("view/canvas_center_snap", False) and position == extent / 2
                if (axis, position) in current or edge or center:
                    lines.append(
                        ((position, 0), (position, height))
                        if axis == "vertical"
                        else ((0, position), (width, position))
                    )
        return lines

    def draw(self, painter: QPainter) -> None:
        lines = self.lines()
        if not lines:
            return
        painter.save()
        try:
            painter.setClipRect(
                QRectF(0, 0, self.widget.state.width, self.widget.state.height),
                Qt.ClipOperation.IntersectClip,
            )
            painter.setOpacity(1)
            for color, width in ((QColor(25, 15, 35, 220), 5.0), (QColor(255, 80, 200), 2.5)):
                pen = QPen(color, width)
                pen.setCosmetic(True)
                painter.setPen(pen)
                for start, end in lines:
                    painter.drawLine(QPointF(*start), QPointF(*end))
        finally:
            painter.restore()
