"""Document-wide speaker registry edits share the existing book history."""

from copy import deepcopy
from dataclasses import replace

from loomstroke.plugins.vector_tools.common import ensure_vector_element_ids

from .document_text_objects import replace_text_objects, text_entries
from .japanese_text.speaker_styles import (
    NAMESPACE,
    REFERENCE,
    apply_style,
    merge_registry,
    read_registry,
    validate_registry,
)
from .page_history_memory import estimate_metadata_payload_bytes


def bind_speaker_styles(pages, metadata):
    """Wire namespace-scoped ports without changing the HostAPI protocol."""
    pages.speaker_styles = SpeakerStyleService(
        pages,
        read=lambda: read_registry(metadata._metadata_root()),
        transition=lambda updates: metadata._apply_namespace_transition(
            updates,
            namespace_key=NAMESPACE,
            read_fn=read_registry,
            merge_fn=merge_registry,
            change_type="book.speaker_styles_changed",
        ),
        restore=lambda snapshot: metadata._restore_namespace(
            snapshot,
            namespace_key=NAMESPACE,
            read_fn=read_registry,
            change_type="book.speaker_styles_changed",
        ),
    )


class SpeakerStyleService:
    def __init__(self, pages, *, read, transition, restore):
        self.pages, self._read, self._transition, self._restore = pages, read, transition, restore

    def read(self):
        registry = self._read()
        validate_registry(registry)
        return deepcopy(registry)

    def update(self, registry):
        validate_registry(registry)
        return self.pages._book_history.execute(_UpdateStyles(self, registry))

    def usage_counts(self):
        counts = {style["style_id"]: 0 for style in self.read().get("styles", [])}
        for _, _, obj, _ in text_entries(self.pages).values():
            reference = obj.meta["text_tool"].get(REFERENCE, {})
            key = reference.get("style_id") if isinstance(reference, dict) else None
            if key in counts:
                counts[key] += 1
        return counts


class _UpdateStyles:
    label_key = "book.speaker_styles_changed"
    affected_page_ids = ()

    def __init__(self, service, registry):
        self.service = service
        self.registry = deepcopy(registry)
        self.transition = None
        self.before, self.after = {}, {}

    def apply(self, pages):
        if self.transition is not None:
            previous = replace_text_objects(pages, self.after)
            try:
                if not self.service._restore(self.transition.after):
                    raise ValueError("Speaker styles could not be restored.")
            except Exception:
                replace_text_objects(pages, previous)
                raise
            return True
        styles = {s["style_id"]: s for s in self.registry.get("styles", [])}
        updates = {}
        for key, (_, _, obj, locked) in text_entries(pages).items():
            payload = obj.meta["text_tool"]
            reference = payload.get(REFERENCE)
            if not isinstance(reference, dict):
                continue
            style = styles.get(reference.get("style_id"))
            if style is None:
                updated = deepcopy(payload)
                updated.pop(REFERENCE, None)  # Keep the appearance when deleting a style.
            else:
                updated = apply_style(payload, style)
            if updated != payload:
                if locked:
                    raise ValueError("A linked speaker text frame is locked.")
                updates[key] = ensure_vector_element_ids(
                    replace(obj, meta={**deepcopy(obj.meta), "text_tool": updated})
                )
        previous = replace_text_objects(pages, updates)
        try:
            transition = self.service._transition(self.registry)
        except Exception:
            replace_text_objects(pages, previous)
            raise
        if transition is None:
            replace_text_objects(pages, previous)
            return False
        self.transition, self.before, self.after = transition, previous, updates
        self.affected_page_ids = tuple(sorted({key[0] for key in updates}))
        return True

    def revert(self, pages):
        previous = replace_text_objects(pages, self.before)
        try:
            if not self.service._restore(self.transition.before):
                raise ValueError("Speaker styles could not be restored.")
        except Exception:
            replace_text_objects(pages, previous)
            raise
        return True

    def estimated_retained_bytes(self, seen):
        payloads = [self.registry]
        payloads.extend(
            obj.meta for objects in (self.before, self.after) for obj in objects.values()
        )
        if self.transition:
            payloads.extend(
                [self.transition.before.namespace_value, self.transition.after.namespace_value]
            )
        return estimate_metadata_payload_bytes(payloads, seen=seen)
