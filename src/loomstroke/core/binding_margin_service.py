"""Generate binding-margin guides through the existing page view-state merge."""

from __future__ import annotations

from typing import Any

from .binding_margin_geometry import binding_margin_position, current_binding_margin_mm
from .book_layout_service import BookLayoutService
from .book_spread_layout import BookLayout, resolve_spreads


class BindingMarginService:
    def __init__(self, pages: Any, layout: BookLayout) -> None:
        self.pages, self.layout = pages, layout

    def _inputs(self) -> tuple[dict[str, Any], dict[str, str]]:
        summaries = {page.id: page for page in self.pages.list_pages()}
        getter = getattr(self.pages, "get_page_metadata", None)
        metadata = {page_id: getter(page_id) if callable(getter) else None for page_id in summaries}
        attributes = BookLayoutService().resolve_page_attributes_by_id(metadata)
        sides = {
            slot.page_id: slot.physical_side
            for spread in resolve_spreads(list(summaries), self.layout, attributes)
            for slot in spread.slots
            if slot.page_id is not None
        }
        return summaries, sides

    def current_width_mm(self) -> float:

        summaries, sides = self._inputs()
        page_id = self.pages.get_active_page_id()
        if page_id not in summaries:
            return 0.0
        view = self.pages.get_page_view_state(page_id) or {}
        guides = view.get("guides", [])
        if not isinstance(guides, list) or any(not isinstance(g, dict) for g in guides):
            return 0.0
        return current_binding_margin_mm(
            guides, dpi_x=summaries[page_id].canvas.dpi[0], physical_side=sides[page_id]
        )

    def page_inputs(self) -> tuple[dict[str, Any], dict[str, str]]:
        """Read-only page specifications and CT-06 physical sides for format operations."""
        return self._inputs()

    def apply(self, width_mm: float, *, all_pages: bool = False) -> int:
        summaries, sides = self._inputs()
        targets = list(summaries) if all_pages else [self.pages.get_active_page_id()]
        patches = []
        for page_id in targets:
            if page_id not in summaries:
                raise ValueError("Page is unavailable")
            view = self.pages.get_page_view_state(page_id) or {}
            guides = view.get("guides", [])
            if not isinstance(guides, list) or any(not isinstance(g, dict) for g in guides):
                raise ValueError("Invalid guides")
            spec = summaries[page_id].canvas
            position = binding_margin_position(
                guides,
                width_mm=width_mm,
                dpi_x=spec.dpi[0],
                physical_side=sides[page_id],
                canvas_width=spec.width,
            )
            updated = [g for g in guides if not str(g.get("id", "")).startswith("tpl-gutter-")]
            if position is not None:
                updated.append(
                    {"id": "tpl-gutter-v-1", "orientation": "vertical", "position": position}
                )
            if updated != guides:
                patches.append((page_id, updated))
        for page_id, guides in patches:
            self.pages.update_page_view_state(page_id, {"guides": guides}, mark_dirty=True)
        return len(patches)
