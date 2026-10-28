"""Guide-only operations over the existing page template library and page merge API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any
from uuid import uuid4

from .guide_layout_geometry import map_guide_positions
from .page_templates import PageTemplate, PageTemplateStore


def _stored_guides(template: PageTemplate) -> list[dict[str, Any]] | None:
    metadata = template.metadata or {}
    view = metadata.get("view_state")
    raw = view.get("guides") if isinstance(view, Mapping) else None
    return raw if isinstance(raw, list) else None


def _positions(raw: list[dict[str, Any]]) -> list:
    try:
        return [(g["orientation"], float(g["position"])) for g in raw]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Invalid guide template") from error


class GuideTemplateService:
    def __init__(self, store: PageTemplateStore, pages: Any) -> None:
        self.store, self.pages = store, pages

    def templates(self) -> tuple[PageTemplate, ...]:
        return tuple(
            template
            for template in self.store.list_templates()
            if template.canvas is not None and _stored_guides(template) is not None
        )

    def save(self, name: str) -> PageTemplate:
        if not name.strip():
            raise ValueError("Template name is empty")
        page_id = self.pages.get_active_page_id()
        spec = self.pages.get_active_canvas_spec()
        view = self.pages.get_page_view_state(page_id) or {}
        guides = deepcopy(view.get("guides", []))
        if not isinstance(guides, list):
            raise ValueError("Invalid guides")
        positions = _positions(guides)
        map_guide_positions(
            positions, (spec.width, spec.height), (spec.width, spec.height), proportional=False
        )
        template = PageTemplate(
            id=f"guides.{uuid4().hex}",
            name=name.strip(),
            category="guides",
            source="user",
            canvas_mode="fixed",
            canvas=spec,
            metadata={"view_state": {"guides": guides}},
        )
        return self.store.save_user_template(template)

    def apply(
        self, template_id: str, page_ids: Sequence[str], *, proportional: bool, replace: bool
    ) -> int:
        template = self.store.get(template_id)
        if template is None or template.canvas is None:
            raise ValueError("Guide template is unavailable")
        raw = deepcopy(_stored_guides(template))
        if not isinstance(raw, list):
            raise ValueError("Template contains no guides")
        positions = _positions(raw)
        summaries = {p.id: p for p in self.pages.list_pages()}
        patches = []
        for page_id in dict.fromkeys(page_ids):
            if page_id not in summaries:
                raise ValueError("Target page is unavailable")
            target = summaries[page_id].canvas
            before = (self.pages.get_page_view_state(page_id) or {}).get("guides", [])
            if not replace and not isinstance(before, list):
                raise ValueError("Invalid target guides")
            mapped = map_guide_positions(
                positions,
                (template.canvas.width, template.canvas.height),
                (target.width, target.height),
                proportional=proportional,
            )
            guides = []
            for entry, (orientation, position) in zip(raw, mapped, strict=True):
                prefix = "g_"
                old_id = str(entry.get("id", ""))
                if old_id.startswith("tpl-") and len(old_id.split("-")) >= 3:
                    prefix = "-".join(old_id.split("-")[:2]) + "-"
                guides.append(
                    {
                        **entry,
                        "id": prefix + uuid4().hex[:12],
                        "orientation": orientation,
                        "position": position,
                    }
                )
            patches.append((page_id, guides))
        for page_id, guides in patches:
            # Read at write time: another writer may have merged viewport keys.
            view = self.pages.get_page_view_state(page_id) or {}
            before = deepcopy(view.get("guides", []))
            self.pages.update_page_view_state(
                page_id, {"guides": guides if replace else [*before, *guides]}, mark_dirty=True
            )
        return len(patches)
