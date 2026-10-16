"""Expand a document vector batch to complete linked stories before mutation."""

from .document_story import assign_document_story
from .linked_story import story_of


def expand_story_batch(pages, replacements):
    result = dict(replacements)
    groups = {}
    for key, obj in replacements.items():
        story = story_of(obj)
        if story is not None:
            groups.setdefault(story["id"], []).append((key, obj))
    for changes in groups.values():
        source_key, source = changes[0]
        payload = source.meta["text_tool"]
        # One story has one text stream. Contradictory row edits cannot be merged silently.
        for _, obj in changes[1:]:
            other = obj.meta["text_tool"]
            if any(
                payload.get(name) != other.get(name)
                for name in ("content", "document", "tracking_runs", "line_height_runs")
            ):
                raise ValueError("Conflicting edits to one linked text story.")
        members = pages.document_stories.chain(source_key)
        members = [(key, replacements.get(key, obj)) for key, obj in members]
        result.update(assign_document_story(members, payload, story_of(source)["id"]))
    return result
