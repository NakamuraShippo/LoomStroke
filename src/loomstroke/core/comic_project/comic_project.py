"""Project root record (CT-17 §7.1)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .document_link import DocumentLink

__all__ = ["ComicProject"]


@dataclass(frozen=True, slots=True)
class ComicProject:
    project_id: str
    title: str
    reading_direction: str = "rtl"
    document_links: tuple[DocumentLink, ...] = field(default_factory=tuple)
    workflow_profile_id: str = "manga.default"
    revision: int = 1
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("project_id is required")
        if not self.title.strip():
            raise ValueError("title is required")
        if self.reading_direction not in ("rtl", "ltr"):
            raise ValueError("reading_direction must be rtl or ltr")
        if self.revision < 1:
            raise ValueError("revision must be >= 1")

    @property
    def primary_document_id(self) -> str | None:
        return self.document_links[0].document_id if self.document_links else None
