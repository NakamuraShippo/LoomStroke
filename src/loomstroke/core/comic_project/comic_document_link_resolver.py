"""Project <-> document link health (CT-17 §3 / §6 ``ComicDocumentLinkResolver``).

Given the document's current page ids and the text of its text objects,
report page entries pointing at missing pages, dialogues without a text
object, text refs whose object vanished, one object claimed by two
dialogues, and script/canvas text differences.  Never "fixes" anything
by guessing (spec 17 §13: no silent re-linking).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .comic_project_snapshot import ComicProjectSnapshot
from .link_report import LinkReport

__all__ = ["ComicDocumentLinkResolver"]


class ComicDocumentLinkResolver:
    @staticmethod
    def normalize(text: str) -> str:
        return " ".join(text.split())

    def resolve(
        self,
        snapshot: ComicProjectSnapshot,
        *,
        page_ids: Sequence[str],
        text_by_object: Mapping[str, Mapping[str, str]],
    ) -> LinkReport:
        """``text_by_object[page_id][object_id]`` = current canvas text."""
        known_pages = set(page_ids)
        unresolved_pages = tuple(
            entry.entry_id
            for entry in snapshot.page_entries_in_order()
            if entry.page_id and entry.page_id not in known_pages
        )
        unplaced: list[str] = []
        unresolved_refs: list[tuple[str, str, str]] = []
        diffs: list[tuple[str, str, str, str, str]] = []
        seen: dict[tuple[str, str], str] = {}
        duplicates: list[tuple[str, str]] = []
        for dialogue in snapshot.dialogues_in_order():
            if not dialogue.text_object_refs:
                unplaced.append(dialogue.dialogue_id)
                continue
            for page_id, object_id in dialogue.text_object_refs:
                owner = seen.get((page_id, object_id))
                if owner is not None and owner != dialogue.dialogue_id:
                    if (page_id, object_id) not in duplicates:
                        duplicates.append((page_id, object_id))
                else:
                    seen[(page_id, object_id)] = dialogue.dialogue_id
                objects = text_by_object.get(page_id)
                if objects is None or object_id not in objects:
                    unresolved_refs.append((dialogue.dialogue_id, page_id, object_id))
                    continue
                canvas_text = objects[object_id]
                if self.normalize(canvas_text) != self.normalize(dialogue.text):
                    diffs.append(
                        (dialogue.dialogue_id, page_id, object_id, dialogue.text, canvas_text)
                    )
        return LinkReport(
            unresolved_page_entries=unresolved_pages,
            unplaced_dialogues=tuple(unplaced),
            unresolved_text_refs=tuple(unresolved_refs),
            duplicate_placements=tuple(duplicates),
            text_diffs=tuple(diffs),
        )
