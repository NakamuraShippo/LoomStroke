"""Result of resolving project links against a document (CT-17 §3 / §13)."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["LinkReport"]


@dataclass(frozen=True, slots=True)
class LinkReport:
    """Ids only — no dialogue text leaves the resolver except in ``text_diffs``
    (script vs canvas), which the UI shows on demand."""

    unresolved_page_entries: tuple[str, ...] = ()
    unplaced_dialogues: tuple[str, ...] = ()
    unresolved_text_refs: tuple[tuple[str, str, str], ...] = ()  # (dialogue_id, page_id, object_id)
    duplicate_placements: tuple[tuple[str, str], ...] = ()  # (page_id, object_id) used twice
    text_diffs: tuple[tuple[str, str, str, str, str], ...] = field(default_factory=tuple)
    # (dialogue_id, page_id, object_id, script_text, canvas_text)

    @property
    def is_clean(self) -> bool:
        return not (
            self.unresolved_page_entries
            or self.unresolved_text_refs
            or self.duplicate_placements
            or self.text_diffs
        )

    def counts(self) -> dict[str, int]:
        return {
            "unresolved_pages": len(self.unresolved_page_entries),
            "unplaced": len(self.unplaced_dialogues),
            "unresolved_text_refs": len(self.unresolved_text_refs),
            "duplicates": len(self.duplicate_placements),
            "text_diffs": len(self.text_diffs),
        }
