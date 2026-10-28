"""Guarded cross-page vector batches on the existing document history."""

from copy import deepcopy

from .document_vector_objects import (
    _call_vector_scope,
    _require_vector_scope,
    _vector_batch_scope,
    _VectorBatchSuperseded,
    replace_vector_objects,
    vector_entries,
)
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
        lease = pages._hold_page_residency()
        try:
            return self._apply_resident(pages)
        finally:
            lease.release()

    def _apply_resident(self, pages):
        scope = _vector_batch_scope(pages, self.affected_page_ids)
        try:
            if self.before is None:
                entries = _call_vector_scope(
                    scope, vector_entries, pages, page_ids=set(self.affected_page_ids)
                )
                if self.after.keys() != self.expected.keys() or any(
                    key not in entries or entries[key][2] != obj
                    for key, obj in self.expected.items()
                ):
                    raise ValueError("Dialogue changed while the batch editor was open.")
                from .japanese_text.story_batch import expand_story_batch  # noqa: PLC0415

                expanded = _call_vector_scope(scope, expand_story_batch, pages, self.after)
                self.after = expanded
                self.affected_page_ids = tuple(sorted({key[0] for key in self.after}))
            previous = replace_vector_objects(pages, self.after)
            _require_vector_scope(scope)
        except _VectorBatchSuperseded:
            return False
        if self.before is None:
            self.before = previous
            self.expected = {}
        return True

    def revert(self, pages):
        try:
            replace_vector_objects(pages, self.before)
        except _VectorBatchSuperseded:
            return False
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
