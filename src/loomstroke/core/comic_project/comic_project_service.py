"""Open / create / apply-and-save facade over the store (CT-17 §6 / §7.2).

``apply`` takes a function of ``(mutator, snapshot) -> snapshot`` so every
edit is one revision-checked metadata transaction; the caller keeps the
returned snapshot as its new truth.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from .comic_project import ComicProject
from .comic_project_mutator import ComicProjectMutator
from .comic_project_snapshot import ComicProjectSnapshot
from .comic_project_store import ComicProjectStore
from .document_link import DocumentLink
from .story_id_factory import StoryIdFactory

__all__ = ["ComicProjectService"]

Change = Callable[[ComicProjectMutator, ComicProjectSnapshot], ComicProjectSnapshot]


class ComicProjectService:
    def __init__(
        self,
        store: ComicProjectStore,
        *,
        ids: StoryIdFactory | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._ids = ids or StoryIdFactory()
        self._mutator = ComicProjectMutator(self._ids)
        self._clock = clock or (lambda: datetime.now(UTC).isoformat())

    @property
    def store(self) -> ComicProjectStore:
        return self._store

    @property
    def mutator(self) -> ComicProjectMutator:
        return self._mutator

    def create_project(
        self,
        title: str,
        *,
        document_id: str | None = None,
        basename: str = "",
        reading_direction: str = "rtl",
    ) -> ComicProjectSnapshot:
        links = (DocumentLink(document_id, basename),) if document_id else ()
        project = ComicProject(
            project_id=self._ids.next_id("project"),
            title=title.strip() or "Untitled",
            reading_direction=reading_direction,
            document_links=links,
            created_at=self._clock(),
        )
        snapshot = ComicProjectMutator.new_project(project)
        return self._store.save(snapshot)

    def open(self, project_id: str) -> ComicProjectSnapshot | None:
        return self._store.load(project_id)

    def list_projects(self) -> tuple[tuple[str, str], ...]:
        return self._store.list_projects()

    def find_for_document(self, document_id: str | None) -> str | None:
        if not document_id:
            return None
        for project_id, _title in self._store.list_projects():
            snapshot = self._store.load(project_id)
            if snapshot is None:
                continue
            if any(link.document_id == document_id for link in snapshot.project.document_links):
                return project_id
        return None

    def apply(self, snapshot: ComicProjectSnapshot, change: Change) -> ComicProjectSnapshot:
        """Run ``change`` and persist with a revision check; returns the saved snapshot."""
        updated = change(self._mutator, snapshot)
        if updated is snapshot or updated.revision == snapshot.revision:
            return snapshot
        return self._store.save(updated, expected_revision=snapshot.revision)
