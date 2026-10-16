"""Atomic linked-frame edits using the existing vector command and history path."""

from copy import deepcopy
from dataclasses import replace

from ..commands import Command
from ..vector_text_layout import TextLayoutCalculator as Layout
from .linked_story import assign_story, combine_payloads, slice_payload, story_objects, story_of


class StoryReplacement:
    def __init__(self, entries):
        self.entries = entries
        self.previous = []

    def apply(self, canvas):
        self.previous = []
        for layer_id, obj in self.entries:
            changed = canvas.replace_vector_object(str(obj.id), obj, layer_id=layer_id)
            if changed is None:
                self.revert(canvas)
                return False
            self.previous.append((layer_id, changed[2]))
            canvas._get_layer_by_id(layer_id).texture_version += 1
        return True

    def revert(self, canvas):
        for layer_id, obj in reversed(self.previous):
            canvas.replace_vector_object(str(obj.id), obj, layer_id=layer_id)
            canvas._get_layer_by_id(layer_id).texture_version += 1
        return True


def prepare_story_replacement(canvas, target_id, new_object):
    story = story_of(new_object)
    if story is None:
        return None
    if story.get("scope") == "document":
        service = getattr(canvas, "_document_story_service", None)
        if service is None:
            raise ValueError("Document text frame services are unavailable.")
        return service.prepare(new_object)
    ids = [frame["id"] for frame in story["frames"]]
    entries = story_objects(canvas, ids)
    entries = [
        (layer_id, new_object if str(obj.id) == target_id else obj) for layer_id, obj in entries
    ]
    return StoryReplacement(assign_story(entries, new_object.meta["text_tool"], story["id"]))


class LinkTextFramesCommand(Command):
    """Link or detach a complete same-page story without merging its layers."""

    def __init__(self, source_id, target_id=None, *, layer_id=None):
        super().__init__(merge_key=None)
        self.source_id, self.target_id = source_id, target_id
        self._layer_id = layer_id
        self.new_object = None  # Existing vector submit routing and history.
        self.replacement = None
        self.error = None

    def apply(self, canvas):
        try:
            source = story_objects(canvas, [self.source_id])[0][1]
            source_story = story_of(source)
            source_ids = (
                [entry["id"] for entry in source_story["frames"]]
                if source_story
                else [self.source_id]
            )
            if self.target_id is None:
                entries = story_objects(canvas, source_ids)
                updated = self._detach(entries)
            else:
                target = story_objects(canvas, [self.target_id])[0][1]
                target_story = story_of(target)
                target_ids = (
                    [entry["id"] for entry in target_story["frames"]]
                    if target_story
                    else [self.target_id]
                )
                if set(source_ids) & set(target_ids):
                    raise ValueError("These frames already belong to the same text story.")
                entries = story_objects(canvas, source_ids + target_ids)
                verticals = {
                    bool(
                        Layout.text_style_defaults(obj.meta["text_tool"]).get(
                            "vertical", obj.meta["text_tool"].get("vertical", False)
                        )
                    )
                    for _, obj in entries
                }
                if len(verticals) != 1:
                    raise ValueError("Linked frames must use the same writing direction.")
                payload = combine_payloads([source.meta["text_tool"], target.meta["text_tool"]])
                updated = assign_story(entries, payload)
            self.replacement = StoryReplacement(updated)
            return self.replacement.apply(canvas)
        except ValueError as error:
            self.error = str(error)
            return False

    def revert(self, canvas):
        return self.replacement.revert(canvas) if self.replacement else True

    @staticmethod
    def _detach(entries):
        from .typography_layout import build_japanese_layout  # noqa: PLC0415

        result = []
        consumed = 0
        for index, (layer_id, obj) in enumerate(entries):
            original = obj.meta["text_tool"]
            layout = build_japanese_layout(original)
            start = min((line.start for line in layout.lines), default=consumed)
            end = max((line.next_start for line in layout.lines), default=start)
            if index == len(entries) - 1:
                end = len(Layout.text_content_from_payload(original))
            payload = slice_payload(original, start, end)
            consumed = end
            payload.pop("linked_story", None)
            result.append(
                (layer_id, replace(obj, meta={**deepcopy(obj.meta), "text_tool": payload}))
            )
        return result
