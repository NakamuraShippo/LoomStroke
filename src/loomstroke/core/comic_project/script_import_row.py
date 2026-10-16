"""One parsed script row before it is applied (CT-17 §5.1 / §13)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ScriptImportRow"]


@dataclass(frozen=True, slots=True)
class ScriptImportRow:
    """``action``: ``add`` / ``update`` / ``unchanged`` / ``invalid``."""

    row_number: int
    action: str
    chapter: str
    page_number: int | None
    panel: int | None
    speaker: str
    text: str
    reading_direction: str | None = None
    dialogue_id: str | None = None
    previous_text: str | None = None
    issues: tuple[str, ...] = ()
