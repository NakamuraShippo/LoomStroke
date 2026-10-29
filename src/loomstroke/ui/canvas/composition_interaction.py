"""Move/delete composition patterns as view preferences, outside artwork history."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt

from ...core.composition_snap import nearest_line_hit, nearest_line_point
from .guide_canvas_bridge import update_guide_overlay


def clear_composition_guides(widget: Any, settings: Any) -> None:
    for key, empty in (
        ("view/composition_overlay_kinds", []),
        ("view/composition_overlay_offsets", {}),
    ):
        if settings.get(key, empty):
            settings.set(key, empty)
    interaction = getattr(widget, "_composition_interaction", None)
    if interaction is not None:
        interaction.cancel()
    update = getattr(widget, "update", None)
    if callable(update):
        update_guide_overlay(widget)


class CompositionInteraction:
    def __init__(self, widget: Any) -> None:
        self.widget = widget
        self.selected: str | None = None
        self.start: tuple[float, float] | None = None
        self.offset = (0.0, 0.0)
        self.original = (0.0, 0.0)
        self.page: Any = None
        self.tool: Any = None
        self.last_snap_target: tuple[str, int | None] | None = None

    def cancel(self) -> None:
        self.start = None
        self.selected = None
        update_guide_overlay(self.widget)

    def _settings(self) -> Any:
        return getattr(getattr(self.widget, "_host_api", None), "settings", None)

    def _page(self) -> Any:
        pages = getattr(getattr(self.widget, "_host_api", None), "pages", None)
        return pages.get_active_page_id() if pages is not None else None

    def snap_position(self, x: float, y: float, w: float = 0, h: float = 0) -> tuple:
        from .composition_overlay import composition_groups  # noqa: PLC0415

        self.last_snap_target = None
        settings = self._settings()
        if settings is None:
            return x, y, False
        groups = composition_groups(self.widget)
        if not groups:
            return x, y, False
        threshold = self.widget.viewport.screen_to_canvas_distance(
            settings.get("view/guides_snap_threshold_px", 8)
        )
        entries = tuple(
            (kind, index, line)
            for kind, group in groups.items()
            for index, line in enumerate(group)
        )
        lines = tuple(entry[2] for entry in entries)
        best = None
        for px, py in dict.fromkeys(((x, y), (x + w, y), (x, y + h), (x + w, y + h))):
            hit = nearest_line_hit((px, py), lines, threshold)
            if hit is None:
                continue
            point, entry_index = hit
            if not (
                0 <= point[0] <= self.widget.state.width
                and 0 <= point[1] <= self.widget.state.height
            ):
                continue
            dx, dy = point[0] - px, point[1] - py
            distance = dx * dx + dy * dy
            if best is None or distance < best[0]:
                best = distance, x + dx, y + dy
                kind, index, _line = entries[entry_index]
                self.last_snap_target = kind, None if kind == "spiral" else index
        return (x, y, False) if best is None else (best[1], best[2], True)

    def _valid(self) -> bool:
        settings = self._settings()
        valid = (
            settings is not None
            and self.page == self._page()
            and self.tool == getattr(self.widget, "_active_tool_id", lambda: None)()
            and settings.get("view/guides_visible", True)
            and not settings.get("view/guides_locked", False)
            and self.selected in settings.get("view/composition_overlay_kinds", [])
        )
        if not valid:
            self.start = None
            self.selected = None
        return valid

    def preview_offset(self, kind: str, saved: tuple) -> tuple:
        if self._valid() and kind == self.selected and self.start is not None:
            return self.offset
        return saved

    def press(self, point: Any) -> bool:
        from .composition_overlay import composition_groups  # noqa: PLC0415

        self.start = None
        self.selected = None
        settings = self._settings()
        if settings is None or settings.get("view/guides_locked", False):
            return False
        threshold = self.widget.viewport.screen_to_canvas_distance(
            settings.get("view/guides_hit_slop_px", 6)
        )
        position = point.x(), point.y()
        best = None
        for kind, lines in composition_groups(self.widget).items():
            hit = nearest_line_point(position, lines, threshold)
            if hit is not None:
                distance = (hit[0] - position[0]) ** 2 + (hit[1] - position[1]) ** 2
                if best is None or distance < best[0]:
                    best = distance, kind
        if best is None:
            return False
        self.selected = best[1]
        offsets = settings.get("view/composition_overlay_offsets", {})
        offsets = offsets if isinstance(offsets, dict) else {}
        try:
            dx, dy = map(float, offsets.get(self.selected, (0.0, 0.0)))
        except (TypeError, ValueError):
            dx, dy = 0.0, 0.0
        self.original = self.offset = dx, dy
        self.page = self._page()
        self.tool = getattr(self.widget, "_active_tool_id", lambda: None)()
        self.start = position
        update_guide_overlay(self.widget)
        return True

    def move(self, point: Any) -> bool:
        if not self._valid() or self.start is None:
            return False
        self.offset = (
            self.original[0] + point.x() - self.start[0],
            self.original[1] + point.y() - self.start[1],
        )
        update_guide_overlay(self.widget)
        return True

    def release(self) -> bool:
        if not self._valid() or self.start is None:
            return False
        settings = self._settings()
        saved = settings.get("view/composition_overlay_offsets", {})
        offsets = dict(saved) if isinstance(saved, dict) else {}
        offsets[self.selected] = list(self.offset)
        settings.set("view/composition_overlay_offsets", offsets)
        self.start = None
        update_guide_overlay(self.widget)
        return True

    def key(self, event: Any) -> bool:
        if not self._valid():
            return False
        if event.key() == Qt.Key.Key_Escape:
            self.start = None
            self.selected = None
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            settings = self._settings()
            settings.set(
                "view/composition_overlay_kinds",
                [
                    kind
                    for kind in settings.get("view/composition_overlay_kinds", [])
                    if kind != self.selected
                ],
            )
            self.start = None
            self.selected = None
        else:
            return False
        update_guide_overlay(self.widget)
        return True
