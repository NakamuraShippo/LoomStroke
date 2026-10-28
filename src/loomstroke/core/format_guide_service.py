"""Reapply physical guide roles using page-local DPI and existing merge semantics."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .binding_margin_service import BindingMarginService
from .book_spread_layout import BookLayout
from .comic_document_profile import PhysicalPageLayout
from .format_guide_geometry import layout_from_guides, regenerate_format_guides


class FormatGuideService:
    def __init__(self, pages: Any, book_layout: BookLayout) -> None:
        self.pages = pages
        self.binding = BindingMarginService(pages, book_layout)

    def current_layout(self) -> PhysicalPageLayout:
        spec = self.pages.get_active_canvas_spec()
        view = self.pages.get_page_view_state(self.pages.get_active_page_id()) or {}
        guides = view.get("guides", [])
        return layout_from_guides(
            guides if isinstance(guides, list) else [], (spec.width, spec.height), tuple(spec.dpi)
        )

    def apply(
        self, layout: PhysicalPageLayout, *, gutter_mm: float, all_pages: bool = False
    ) -> int:
        summaries, sides = self.binding.page_inputs()
        targets = list(summaries) if all_pages else [self.pages.get_active_page_id()]
        patches = []
        for page_id in targets:
            if page_id not in summaries:
                raise ValueError("Page is unavailable")
            view = self.pages.get_page_view_state(page_id) or {}
            existing = view.get("guides", [])
            if not isinstance(existing, list) or any(not isinstance(g, dict) for g in existing):
                raise ValueError("Invalid guides")
            spec = summaries[page_id].canvas
            generated = regenerate_format_guides(
                replace(layout, dpi=tuple(spec.dpi)),
                existing,
                size=(spec.width, spec.height),
                gutter_mm=gutter_mm,
                physical_side=sides[page_id],
            )
            if generated != existing:
                patches.append((page_id, generated))
        for page_id, guides in patches:
            self.pages.update_page_view_state(page_id, {"guides": guides}, mark_dirty=True)
        return len(patches)
