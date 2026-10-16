"""Deterministic, local-only book planning from a CT-17 comic project snapshot (CT-22 Phase A).

Dialogue text is referenced by id + hash only; ``page_script`` materialises a
page's script (the single-page planner's marker format) on explicit request.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, ClassVar

from .comic_book_plan import ComicBookPlan
from .comic_page_work_item import ComicPageWorkItem
from .comic_panel_work_item import ComicPanelWorkItem

__all__ = ["ComicBookPlanner"]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class ComicBookPlanner:
    DEFAULT_PANELS_PER_PAGE: ClassVar[int] = 4
    MAX_PANELS_PER_PAGE: ClassVar[int] = 8

    def __init__(
        self, *, default_panels_per_page: int | None = None, max_panels: int | None = None
    ) -> None:
        self._default = max(1, int(default_panels_per_page or self.DEFAULT_PANELS_PER_PAGE))
        self._max = max(self._default, int(max_panels or self.MAX_PANELS_PER_PAGE))

    def plan_from_snapshot(
        self, snapshot: Any, *, style_constraints: Mapping[str, Any] | None = None
    ) -> ComicBookPlan:
        project = snapshot.project
        pages: list[ComicPageWorkItem] = []
        warnings: list[str] = []
        for entry in snapshot.page_entries_in_order():
            panels = self._panels_for_entry(snapshot, entry)
            if not panels:
                warnings.append("page_without_panels")
            pages.append(
                ComicPageWorkItem(
                    entry_id=entry.entry_id,
                    page_number=int(entry.page_number),
                    panels=tuple(panels),
                    page_id=entry.page_id,
                    document_id=entry.document_id,
                    role=str(entry.role or "body"),
                    script_hash=_hash(self.page_script(snapshot, entry.entry_id)),
                )
            )
        if not pages:
            warnings.append("book_without_pages")
        return ComicBookPlan(
            plan_id=f"bookplan-{_hash(project.project_id + ':' + str(snapshot.revision))}",
            project_ref=project.project_id,
            pages=tuple(pages),
            reading_direction=str(project.reading_direction or "rtl"),
            style_constraints=dict(style_constraints or {}),
            source_revision=int(snapshot.revision),
            warnings=tuple(sorted(set(warnings))),
        )

    # -- panels ------------------------------------------------------------------------

    def _panels_for_entry(self, snapshot: Any, entry: Any) -> list[ComicPanelWorkItem]:
        dialogues = list(snapshot.dialogues_for_entry(entry.entry_id))
        declared = list(snapshot.panels_for_entry(entry.entry_id))
        if declared:
            items: list[ComicPanelWorkItem] = []
            for panel in declared:
                refs = [d for d in dialogues if d.panel_id == panel.panel_id]
                items.append(
                    self._item(panel.panel_id, int(panel.reading_order), refs, panel.summary)
                )
            loose = [
                d
                for d in dialogues
                if not d.panel_id or d.panel_id not in {p.panel_id for p in declared}
            ]
            if loose and items:
                last = items[-1]
                items[-1] = self._item(
                    last.panel_id,
                    last.reading_order,
                    [d for d in dialogues if d.panel_id == last.panel_id] + loose,
                    "",
                    keep_hash=last.summary_hash,
                )
            return items
        if not dialogues:
            return [
                self._item(f"{entry.entry_id}-p{i + 1}", i, [], "") for i in range(self._default)
            ]
        count = max(1, min(self._max, len(dialogues)))
        buckets: list[list[Any]] = [[] for _ in range(count)]
        for index, dialogue in enumerate(dialogues):
            buckets[min(count - 1, index * count // len(dialogues))].append(dialogue)
        return [
            self._item(f"{entry.entry_id}-p{i + 1}", i, bucket, "")
            for i, bucket in enumerate(buckets)
        ]

    @staticmethod
    def _item(
        panel_id: str,
        order: int,
        dialogues: list[Any],
        summary: str,
        *,
        keep_hash: str | None = None,
    ) -> ComicPanelWorkItem:
        return ComicPanelWorkItem(
            panel_id=panel_id,
            reading_order=order,
            dialogue_refs=tuple(d.dialogue_id for d in dialogues),
            dialogue_hashes=tuple(_hash(f"{d.speaker}:{d.text}") for d in dialogues),
            summary_hash=(
                keep_hash if keep_hash is not None else (_hash(summary) if summary else "")
            ),
        )

    # -- script materialisation --------------------------------------------------------

    def page_script(self, snapshot: Any, entry_id: str) -> str:
        """Per-page script in the single-page planner's marker format (materialised on demand)."""
        entry = snapshot.page_entries.get(entry_id)
        if entry is None:
            return ""
        lines: list[str] = []
        panels = self._panels_for_entry(snapshot, entry)
        by_id = {d.dialogue_id: d for d in snapshot.dialogues_for_entry(entry_id)}
        for index, panel in enumerate(panels, start=1):
            declared = snapshot.panels.get(panel.panel_id)
            summary = str(getattr(declared, "summary", "") or "") if declared else ""
            lines.append(f"Panel {index}: {summary}".rstrip())
            for ref in panel.dialogue_refs:
                dialogue = by_id.get(ref)
                if dialogue is not None and dialogue.text.strip():
                    lines.append(f"{dialogue.speaker}「{dialogue.text.strip()}」")
        return "\n".join(lines).strip()
