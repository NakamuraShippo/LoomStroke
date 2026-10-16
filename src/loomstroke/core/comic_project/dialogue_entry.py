"""One line of dialogue / narration (CT-17 §7.1).

``text_object_refs`` are ``(page_id, object_id)`` pairs — the only link to
canvas text objects; geometry and rendering stay with the Text Tool.
``review_state``: ``draft`` / ``needs_review`` / ``approved``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

__all__ = ["DialogueEntry"]


@dataclass(frozen=True, slots=True)
class DialogueEntry:
    dialogue_id: str
    order_key: str
    speaker: str
    text: str
    reading_direction: str = "vertical"
    style_ref: str | None = None
    entry_id: str | None = None
    panel_id: str | None = None
    text_object_refs: tuple[tuple[str, str], ...] = ()
    revision: int = 1
    review_state: str = "draft"
    notes: str = ""

    REVIEW_STATES: ClassVar[tuple[str, ...]] = ("draft", "needs_review", "approved")

    @property
    def is_placed(self) -> bool:
        return bool(self.text_object_refs)

    @property
    def is_assigned(self) -> bool:
        return bool(self.entry_id)
