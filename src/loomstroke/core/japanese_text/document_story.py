"""Page-qualified linked text frames using the existing glyph layout contract."""

import json
from copy import deepcopy
from dataclasses import replace

from loomstroke.plugins.vector_tools.common import ensure_vector_element_ids

from ..document_text_objects import replace_text_objects, text_entries
from ..page_history_memory import estimate_metadata_payload_bytes
from ..path_text.metadata import path_settings
from ..vector_text_layout import TextLayoutCalculator as Layout
from .linked_story import assign_story, combine_payloads, story_of
from .story_commands import LinkTextFramesCommand


def document_story(obj):
    story = story_of(obj)
    return story if story and story.get("scope") == "document" else None


def frame_key(frame):
    return (frame["page_id"], frame["layer_id"], frame["object_id"])


def assign_document_story(entries, payload, story_id=None):
    aliases = [
        (key[1], replace(obj, id=json.dumps(key, ensure_ascii=False))) for key, obj in entries
    ]
    assigned = assign_story(aliases, payload, story_id)
    result = {}
    for (key, original), (_, obj) in zip(entries, assigned, strict=True):
        story = obj.meta["text_tool"]["linked_story"]
        story["scope"] = "document"
        for frame, (target, _) in zip(story["frames"], entries, strict=True):
            frame.update(page_id=target[0], layer_id=target[1], object_id=target[2])
        result[key] = ensure_vector_element_ids(replace(obj, id=original.id))
    return result


class DocumentStoryReplacement:
    def __init__(self, service, replacements):
        self.service, self.replacements = service, replacements
        self.previous = {}

    def apply(self, canvas):
        self.previous = self.service.replace(self.replacements)
        return bool(self.replacements)

    def revert(self, canvas):
        self.service.replace(self.previous)
        return True


class DocumentStoryService:
    def __init__(self, pages):
        self.pages = pages
        self.updating = False

    def replace(self, replacements, *, geometry=False):
        self.updating = True
        try:
            return replace_text_objects(self.pages, replacements, allow_locked=geometry)
        finally:
            self.updating = False

    def entries(self, keys):
        objects = text_entries(self.pages, page_ids={key[0] for key in keys})
        result = []
        for key in keys:
            found = objects.get(tuple(key))
            if found is None or found[3]:
                raise ValueError("A linked text frame is missing or locked.")
            obj = found[2]
            if path_settings(obj.meta.get("text_tool")) is not None:
                raise ValueError("The text frames could not be linked.")
            result.append((tuple(key), obj))
        return result

    def targets(self, current):
        if path_settings(current.meta.get("text_tool")) is not None:
            return []
        own = story_of(current)
        excluded = (
            {frame_key(frame) for frame in own["frames"]}
            if own and own.get("scope") == "document"
            else set()
        )
        local_ids = (
            {frame["id"] for frame in own["frames"]} if own and not excluded else {str(current.id)}
        )
        return [
            (key, Layout.text_content_from_payload(obj.meta["text_tool"]).replace("\n", " ")[:32])
            for key, (_, _, obj, locked) in text_entries(self.pages).items()
            if key not in excluded
            and not locked
            and path_settings(obj.meta.get("text_tool")) is None
            and (key[0] != self.pages.get_active_page_id() or str(obj.id) not in local_ids)
        ]

    def edit_entries(self, new_object):
        story = document_story(new_object)
        entries = self.entries([frame_key(frame) for frame in story["frames"]])
        current = self.pages.get_active_page_id()
        if not any(key[0] == current and key[2] == str(new_object.id) for key, _ in entries):
            raise ValueError("This copied text frame must be detached before editing its story.")
        return entries

    def prepare(self, new_object):
        story = document_story(new_object)
        entries = self.edit_entries(new_object)
        current = self.pages.get_active_page_id()
        entries = [
            (key, new_object if key[0] == current and key[2] == str(new_object.id) else obj)
            for key, obj in entries
        ]
        return DocumentStoryReplacement(
            self, assign_document_story(entries, new_object.meta["text_tool"], story["id"])
        )

    def chain(self, key):
        entry = self.entries([key])[0]
        story = story_of(entry[1])
        if story is None:
            return [entry]
        if story.get("scope") == "document":
            return self.entries([frame_key(frame) for frame in story["frames"]])
        available = text_entries(self.pages)
        keys = []
        for frame in story["frames"]:
            found = [
                candidate
                for candidate in available
                if candidate[0] == key[0] and candidate[2] == frame["id"]
            ]
            if len(found) != 1:
                raise ValueError("A linked text frame is missing or ambiguous.")
            keys.append(found[0])
        return self.entries(keys)

    def link(self, source, target=None):
        return self.pages._book_history.execute(_LinkDocumentFrames(self, source, target))

    def link_ordered(self, keys):
        from .story_order import OrderedDocumentLink  # noqa: PLC0415

        if len(keys) < 2:
            return False
        return self.pages._book_history.execute(OrderedDocumentLink(self, keys))

    def refresh_geometry(self, changed=None):
        if self.updating:
            return False
        seeds = [story for obj in changed or [] if (story := document_story(obj))]
        if changed is not None and not seeds:
            return False
        available = text_entries(self.pages)
        if changed is None:
            seeds = [story for _, _, obj, _ in available.values() if (story := document_story(obj))]
        updates = {}
        for story_id in {story["id"] for story in seeds}:
            members = {
                key: obj
                for key, (_, _, obj, _) in available.items()
                if (story := document_story(obj)) and story["id"] == story_id
            }
            if not members:
                continue
            candidates = [s for s in seeds if s["id"] == story_id]
            candidates.extend(document_story(obj) for obj in members.values())
            seed = max(candidates, key=lambda s: len(s["frames"]))
            keys = [frame_key(frame) for frame in seed["frames"]]
            for key in members.keys() - set(keys):
                updates[key] = _detach_copied_member(members[key])
            live = [(key, members[key]) for key in keys if key in members]
            if not live:
                continue
            derived = assign_document_story(live, live[0][1].meta["text_tool"], story_id)
            for key, obj in live:
                new_story = derived[key].meta["text_tool"]["linked_story"]
                if new_story != document_story(obj):
                    meta = deepcopy(obj.meta)
                    meta["text_tool"]["linked_story"] = new_story
                    updates[key] = replace(obj, meta=meta)
        if updates:
            self.replace(updates, geometry=True)
        return bool(updates)


class _LinkDocumentFrames:
    label_key = "text.frames_linked"
    affected_page_ids = ()

    def __init__(self, service, source, target):
        self.service, self.source, self.target = service, source, target
        self.before, self.after = {}, None

    def apply(self, pages):
        if self.after is None:
            source = self.service.chain(self.source)
            if self.target is None:
                detached = LinkTextFramesCommand._detach([(key, obj) for key, obj in source])
                self.after = {key: ensure_vector_element_ids(obj) for key, obj in detached}
            else:
                target = self.service.chain(self.target)
                if {key for key, _ in source} & {key for key, _ in target}:
                    raise ValueError("These frames already belong to the same text story.")
                entries = source + target
                if (
                    len(
                        {
                            bool(
                                Layout.text_style_defaults(obj.meta["text_tool"]).get(
                                    "vertical", False
                                )
                            )
                            for _, obj in entries
                        }
                    )
                    != 1
                ):
                    raise ValueError("Linked frames must use the same writing direction.")
                payload = combine_payloads(
                    [source[0][1].meta["text_tool"], target[0][1].meta["text_tool"]]
                )
                self.after = assign_document_story(entries, payload)
            self.affected_page_ids = tuple(sorted({key[0] for key in self.after}))
        self.before = self.service.replace(self.after)
        return True

    def revert(self, pages):
        self.service.replace(self.before)
        return True

    def estimated_retained_bytes(self, seen):
        return estimate_metadata_payload_bytes(
            [obj.meta for entries in (self.before, self.after or {}) for obj in entries.values()],
            seen=seen,
        )


def _detach_copied_member(obj):
    from .linked_story import slice_payload  # noqa: PLC0415
    from .typography_layout import build_japanese_layout  # noqa: PLC0415

    payload = obj.meta["text_tool"]
    layout = build_japanese_layout(payload)
    start = min((line.start for line in layout.lines), default=0)
    end = max((line.next_start for line in layout.lines), default=start)
    payload = slice_payload(payload, start, end)
    payload.pop("linked_story", None)
    return ensure_vector_element_ids(
        replace(obj, meta={**deepcopy(obj.meta), "text_tool": payload})
    )
