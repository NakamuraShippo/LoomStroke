"""A planned page, optionally linked to a PageService page (CT-17 §7.1).

``status`` is a stable workflow stage key (``name`` / ``draft`` / ``ink``
/ ``tone`` / ``lettering`` / ``review`` / ``done``); the UI translates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

__all__ = ["ComicPageEntry"]


@dataclass(frozen=True, slots=True)
class ComicPageEntry:
    entry_id: str
    chapter_id: str
    order_key: str
    page_number: int
    document_id: str | None = None
    page_id: str | None = None
    role: str = "body"
    status: str = "name"
    notes: str = ""

    STAGES: ClassVar[tuple[str, ...]] = (
        "name",
        "draft",
        "ink",
        "tone",
        "lettering",
        "review",
        "done",
    )

    @property
    def is_linked(self) -> bool:
        return bool(self.page_id)
