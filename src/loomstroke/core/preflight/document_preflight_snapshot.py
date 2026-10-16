"""Immutable document view handed to the preflight service (CT-16 §7 / §8.2).

Built on the main thread from host state; rules only ever see this.
``dirty_signature`` records which pages were dirty at snapshot time so
the export guard can tell a report has gone stale (spec 16 §14).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .page_preflight_snapshot import PagePreflightSnapshot

__all__ = ["DocumentPreflightSnapshot"]


@dataclass(frozen=True, slots=True)
class DocumentPreflightSnapshot:
    document_id: str
    document_name: str = ""
    pages: tuple[PagePreflightSnapshot, ...] = field(default_factory=tuple)
    reading_direction: str = "ltr"
    has_color_profile: bool = False
    dirty_signature: str = ""

    def page(self, page_id: str) -> PagePreflightSnapshot | None:
        for page in self.pages:
            if page.page_id == page_id:
                return page
        return None

    @property
    def page_ids(self) -> tuple[str, ...]:
        return tuple(page.page_id for page in self.pages)
