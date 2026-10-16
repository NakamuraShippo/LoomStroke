"""Guarded cross-page vector batches on the existing document history."""

from copy import deepcopy

from .document_vector_objects import replace_vector_objects, vector_entries
from .page_history_memory import estimate_metadata_payload_bytes


class DocumentVectorBatch:
    label_key = "comic.dialogue_batch"

    def __init__(self, replacements, expected):
        self.after = deepcopy(replacements)
        self.expected = deepcopy(expected)
        self.before = None
        self.affected_page_ids = tuple(sorted({key[0] for key in replacements}))

    def apply(self, pages):
        if not self.after:
            return False
        if self.before is None:
            entries = vector_entries(pages, page_ids=set(self.affected_page_ids))
            if self.after.keys() != self.expected.keys() or any(
                key not in entries or entries[key][2] != obj for key, obj in self.expected.items()
            ):
                raise ValueError("Dialogue changed while the batch editor was open.")
            from .japanese_text.story_batch import expand_story_batch  # noqa: PLC0415

            self.after = expand_story_batch(pages, self.after)
            self.affected_page_ids = tuple(sorted({key[0] for key in self.after}))
        previous = replace_vector_objects(pages, self.after)
        if self.before is None:
            self.before = previous
            self.expected = {}
        return True

    def revert(self, pages):
        replace_vector_objects(pages, self.before)
        return True

    def estimated_retained_bytes(self, seen):
        return estimate_metadata_payload_bytes(
            [
                obj.meta
                for group in (self.after, self.before or {}, self.expected)
                for obj in group.values()
            ],
            seen=seen,
        )
