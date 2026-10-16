"""Pure edits on a project snapshot (CT-17 §7.2 ``apply_metadata_change``).

Every method returns a *new* snapshot with ``project.revision + 1``; the
input is never modified.  Ids come from the injected factory, order keys
from :class:`StoryOrderKey`, so inserting never renumbers neighbours.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from .comic_page_entry import ComicPageEntry
from .comic_panel_entry import ComicPanelEntry
from .comic_project import ComicProject
from .comic_project_snapshot import ComicProjectSnapshot
from .dialogue_entry import DialogueEntry
from .document_link import DocumentLink
from .production_task import ProductionTask
from .story_chapter import StoryChapter
from .story_id_factory import StoryIdFactory
from .story_order_key import StoryOrderKey

__all__ = ["ComicProjectMutator"]


class ComicProjectMutator:
    def __init__(self, ids: StoryIdFactory | None = None) -> None:
        self._ids = ids or StoryIdFactory()

    # -- helpers -------------------------------------------------------------------

    @staticmethod
    def _bump(
        snapshot: ComicProjectSnapshot, **collections: Mapping[str, Any]
    ) -> ComicProjectSnapshot:
        project = replace(snapshot.project, revision=snapshot.project.revision + 1)
        values = {
            "chapters": snapshot.chapters,
            "scenes": snapshot.scenes,
            "page_entries": snapshot.page_entries,
            "panels": snapshot.panels,
            "dialogues": snapshot.dialogues,
            "tasks": snapshot.tasks,
        }
        values.update(collections)
        return ComicProjectSnapshot(project=project, **values)  # type: ignore[arg-type]

    @staticmethod
    def _key_after(items: Iterable[Any]) -> str:
        keys = sorted(item.order_key for item in items)
        return StoryOrderKey.after(keys[-1]) if keys else StoryOrderKey.first()

    @staticmethod
    def _key_between(items: Iterable[Any], after_key: str | None) -> str:
        keys = sorted(item.order_key for item in items)
        if after_key is None:
            return StoryOrderKey.between(None, keys[0]) if keys else StoryOrderKey.first()
        later = [key for key in keys if key > after_key]
        return StoryOrderKey.between(after_key, later[0] if later else None)

    # -- project -------------------------------------------------------------------

    def set_title(self, snapshot: ComicProjectSnapshot, title: str) -> ComicProjectSnapshot:
        project = replace(snapshot.project, title=title.strip(), revision=snapshot.revision + 1)
        return replace(snapshot, project=project)

    def link_document(
        self,
        snapshot: ComicProjectSnapshot,
        document_id: str,
        *,
        basename: str = "",
        content_hash: str = "",
    ) -> ComicProjectSnapshot:
        links = tuple(
            link for link in snapshot.project.document_links if link.document_id != document_id
        )
        links = (DocumentLink(document_id, basename, content_hash), *links)
        project = replace(snapshot.project, document_links=links, revision=snapshot.revision + 1)
        return replace(snapshot, project=project)

    # -- chapters -----------------------------------------------------------------

    def add_chapter(
        self, snapshot: ComicProjectSnapshot, title: str, *, after_chapter_id: str | None = None
    ) -> tuple[ComicProjectSnapshot, str]:
        chapter_id = self._ids.next_id("chapter")
        after_key = snapshot.chapters[after_chapter_id].order_key if after_chapter_id else None
        key = (
            self._key_between(snapshot.chapters.values(), after_key)
            if after_chapter_id
            else self._key_after(snapshot.chapters.values())
        )
        chapter = StoryChapter(chapter_id, key, title.strip() or "Chapter")
        chapters = {**snapshot.chapters, chapter_id: chapter}
        return self._bump(snapshot, chapters=chapters), chapter_id

    def rename_chapter(
        self, snapshot: ComicProjectSnapshot, chapter_id: str, title: str
    ) -> ComicProjectSnapshot:
        chapter = replace(snapshot.chapters[chapter_id], title=title.strip() or "Chapter")
        return self._bump(snapshot, chapters={**snapshot.chapters, chapter_id: chapter})

    def remove_chapter(
        self, snapshot: ComicProjectSnapshot, chapter_id: str
    ) -> ComicProjectSnapshot:
        entry_ids = {
            e.entry_id for e in snapshot.page_entries.values() if e.chapter_id == chapter_id
        }
        chapters = {k: v for k, v in snapshot.chapters.items() if k != chapter_id}
        page_entries = {k: v for k, v in snapshot.page_entries.items() if k not in entry_ids}
        panels = {k: v for k, v in snapshot.panels.items() if v.entry_id not in entry_ids}
        dialogues = {
            k: (replace(v, entry_id=None, panel_id=None) if v.entry_id in entry_ids else v)
            for k, v in snapshot.dialogues.items()
        }
        scenes = {k: v for k, v in snapshot.scenes.items() if v.chapter_id != chapter_id}
        return self._bump(
            snapshot,
            chapters=chapters,
            page_entries=page_entries,
            panels=panels,
            dialogues=dialogues,
            scenes=scenes,
        )

    # -- page entries --------------------------------------------------------------

    def add_page_entry(
        self,
        snapshot: ComicProjectSnapshot,
        chapter_id: str,
        *,
        page_number: int | None = None,
        page_id: str | None = None,
        document_id: str | None = None,
        role: str = "body",
    ) -> tuple[ComicProjectSnapshot, str]:
        if chapter_id not in snapshot.chapters:
            raise KeyError(chapter_id)
        entry_id = self._ids.next_id("page")
        existing = snapshot.page_entries_for_chapter(chapter_id)
        number = (
            page_number
            if page_number is not None
            else (existing[-1].page_number + 1 if existing else 1)
        )
        entry = ComicPageEntry(
            entry_id=entry_id,
            chapter_id=chapter_id,
            order_key=self._key_after(existing),
            page_number=int(number),
            document_id=document_id,
            page_id=page_id,
            role=role,
        )
        return (
            self._bump(snapshot, page_entries={**snapshot.page_entries, entry_id: entry}),
            entry_id,
        )

    def link_page(
        self,
        snapshot: ComicProjectSnapshot,
        entry_id: str,
        *,
        document_id: str | None,
        page_id: str,
    ) -> ComicProjectSnapshot:
        entry = replace(snapshot.page_entries[entry_id], document_id=document_id, page_id=page_id)
        return self._bump(snapshot, page_entries={**snapshot.page_entries, entry_id: entry})

    def unlink_page(self, snapshot: ComicProjectSnapshot, entry_id: str) -> ComicProjectSnapshot:
        entry = replace(snapshot.page_entries[entry_id], document_id=None, page_id=None)
        return self._bump(snapshot, page_entries={**snapshot.page_entries, entry_id: entry})

    def set_page_status(
        self, snapshot: ComicProjectSnapshot, entry_id: str, status: str
    ) -> ComicProjectSnapshot:
        if status not in ComicPageEntry.STAGES:
            raise ValueError(f"unknown stage: {status}")
        entry = replace(snapshot.page_entries[entry_id], status=status)
        return self._bump(snapshot, page_entries={**snapshot.page_entries, entry_id: entry})

    def bulk_set_page_status(
        self, snapshot: ComicProjectSnapshot, entry_ids: Iterable[str], status: str
    ) -> tuple[ComicProjectSnapshot, int]:
        if status not in ComicPageEntry.STAGES:
            raise ValueError(f"unknown stage: {status}")
        updated = dict(snapshot.page_entries)
        count = 0
        for entry_id in entry_ids:
            entry = updated.get(entry_id)
            if entry is not None and entry.status != status:
                updated[entry_id] = replace(entry, status=status)
                count += 1
        if not count:
            return snapshot, 0
        return self._bump(snapshot, page_entries=updated), count

    def remove_page_entry(
        self, snapshot: ComicProjectSnapshot, entry_id: str
    ) -> ComicProjectSnapshot:
        page_entries = {k: v for k, v in snapshot.page_entries.items() if k != entry_id}
        panels = {k: v for k, v in snapshot.panels.items() if v.entry_id != entry_id}
        dialogues = {
            k: (replace(v, entry_id=None, panel_id=None) if v.entry_id == entry_id else v)
            for k, v in snapshot.dialogues.items()
        }
        return self._bump(snapshot, page_entries=page_entries, panels=panels, dialogues=dialogues)

    # -- panels --------------------------------------------------------------------

    def add_panel(
        self, snapshot: ComicProjectSnapshot, entry_id: str, *, summary: str = ""
    ) -> tuple[ComicProjectSnapshot, str]:
        if entry_id not in snapshot.page_entries:
            raise KeyError(entry_id)
        panel_id = self._ids.next_id("panel")
        order = len(snapshot.panels_for_entry(entry_id)) + 1
        panel = ComicPanelEntry(panel_id, entry_id, order, summary=summary)
        return self._bump(snapshot, panels={**snapshot.panels, panel_id: panel}), panel_id

    # -- dialogues -----------------------------------------------------------------

    def add_dialogue(
        self,
        snapshot: ComicProjectSnapshot,
        *,
        speaker: str,
        text: str,
        entry_id: str | None = None,
        panel_id: str | None = None,
        reading_direction: str | None = None,
        after_dialogue_id: str | None = None,
        dialogue_id: str | None = None,
    ) -> tuple[ComicProjectSnapshot, str]:
        if entry_id is not None and entry_id not in snapshot.page_entries:
            raise KeyError(entry_id)
        new_id = dialogue_id or self._ids.next_id("dialogue")
        if new_id in snapshot.dialogues:
            raise ValueError(f"dialogue id already exists: {new_id}")
        after_key = snapshot.dialogues[after_dialogue_id].order_key if after_dialogue_id else None
        key = (
            self._key_between(snapshot.dialogues.values(), after_key)
            if after_dialogue_id
            else self._key_after(snapshot.dialogues.values())
        )
        direction = reading_direction or (
            "vertical" if snapshot.project.reading_direction == "rtl" else "horizontal"
        )
        dialogue = DialogueEntry(
            dialogue_id=new_id,
            order_key=key,
            speaker=speaker.strip(),
            text=text,
            reading_direction=direction,
            entry_id=entry_id,
            panel_id=panel_id,
        )
        return self._bump(snapshot, dialogues={**snapshot.dialogues, new_id: dialogue}), new_id

    def update_dialogue(
        self,
        snapshot: ComicProjectSnapshot,
        dialogue_id: str,
        *,
        text: str | None = None,
        speaker: str | None = None,
        review_state: str | None = None,
        reading_direction: str | None = None,
        notes: str | None = None,
    ) -> ComicProjectSnapshot:
        dialogue = snapshot.dialogues[dialogue_id]
        changes: dict[str, Any] = {}
        if text is not None and text != dialogue.text:
            changes["text"] = text
            changes["revision"] = dialogue.revision + 1
        if speaker is not None:
            changes["speaker"] = speaker.strip()
        if review_state is not None:
            if review_state not in DialogueEntry.REVIEW_STATES:
                raise ValueError(f"unknown review state: {review_state}")
            changes["review_state"] = review_state
        if reading_direction is not None:
            changes["reading_direction"] = reading_direction
        if notes is not None:
            changes["notes"] = notes
        if not changes:
            return snapshot
        return self._bump(
            snapshot, dialogues={**snapshot.dialogues, dialogue_id: replace(dialogue, **changes)}
        )

    def assign_dialogue(
        self,
        snapshot: ComicProjectSnapshot,
        dialogue_id: str,
        *,
        entry_id: str | None,
        panel_id: str | None = None,
    ) -> ComicProjectSnapshot:
        if entry_id is not None and entry_id not in snapshot.page_entries:
            raise KeyError(entry_id)
        dialogue = replace(snapshot.dialogues[dialogue_id], entry_id=entry_id, panel_id=panel_id)
        return self._bump(snapshot, dialogues={**snapshot.dialogues, dialogue_id: dialogue})

    def link_text_object(
        self, snapshot: ComicProjectSnapshot, dialogue_id: str, *, page_id: str, object_id: str
    ) -> ComicProjectSnapshot:
        dialogue = snapshot.dialogues[dialogue_id]
        ref = (page_id, object_id)
        if ref in dialogue.text_object_refs:
            return snapshot
        dialogue = replace(dialogue, text_object_refs=(*dialogue.text_object_refs, ref))
        return self._bump(snapshot, dialogues={**snapshot.dialogues, dialogue_id: dialogue})

    def unlink_text_object(
        self, snapshot: ComicProjectSnapshot, dialogue_id: str, *, page_id: str, object_id: str
    ) -> ComicProjectSnapshot:
        dialogue = snapshot.dialogues[dialogue_id]
        refs = tuple(ref for ref in dialogue.text_object_refs if ref != (page_id, object_id))
        if refs == dialogue.text_object_refs:
            return snapshot
        dialogue = replace(dialogue, text_object_refs=refs)
        return self._bump(snapshot, dialogues={**snapshot.dialogues, dialogue_id: dialogue})

    def remove_dialogue(
        self, snapshot: ComicProjectSnapshot, dialogue_id: str
    ) -> ComicProjectSnapshot:
        dialogues = {k: v for k, v in snapshot.dialogues.items() if k != dialogue_id}
        return self._bump(snapshot, dialogues=dialogues)

    def move_dialogue_after(
        self, snapshot: ComicProjectSnapshot, dialogue_id: str, after_dialogue_id: str | None
    ) -> ComicProjectSnapshot:
        others = [d for d in snapshot.dialogues.values() if d.dialogue_id != dialogue_id]
        after_key = snapshot.dialogues[after_dialogue_id].order_key if after_dialogue_id else None
        key = self._key_between(others, after_key)
        dialogue = replace(snapshot.dialogues[dialogue_id], order_key=key)
        return self._bump(snapshot, dialogues={**snapshot.dialogues, dialogue_id: dialogue})

    # -- tasks --------------------------------------------------------------------

    def add_task(
        self,
        snapshot: ComicProjectSnapshot,
        *,
        target_ref: str,
        stage: str,
        assignee_label: str = "",
        due_date: str | None = None,
    ) -> tuple[ComicProjectSnapshot, str]:
        task_id = self._ids.next_id("task")
        task = ProductionTask(
            task_id, target_ref, stage, assignee_label=assignee_label, due_date=due_date
        )
        return self._bump(snapshot, tasks={**snapshot.tasks, task_id: task}), task_id

    def set_task_state(
        self, snapshot: ComicProjectSnapshot, task_id: str, state: str
    ) -> ComicProjectSnapshot:
        if state not in ProductionTask.STATES:
            raise ValueError(f"unknown task state: {state}")
        task = replace(snapshot.tasks[task_id], state=state)
        return self._bump(snapshot, tasks={**snapshot.tasks, task_id: task})

    @staticmethod
    def new_project(project: ComicProject) -> ComicProjectSnapshot:
        return ComicProjectSnapshot(project=project)
