"""Atomic ordered linking using existing story replacement and history ports."""

from ..commands import Command
from ..vector_text_layout import TextLayoutCalculator as Layout
from .document_story import _LinkDocumentFrames, assign_document_story
from .linked_story import assign_story, combine_payloads, story_objects, story_of
from .story_commands import StoryReplacement


def combine_chains(chains):
    entries, payloads, seen = [], [], set()
    for chain in chains:
        keys = {key for key, _ in chain}
        if keys & seen:
            if keys <= seen:
                continue
            raise ValueError("Overlapping text stories.")
        seen.update(keys)
        entries.extend(chain)
        payloads.append(chain[0][1].meta["text_tool"])
    if len(entries) < 2:
        raise ValueError("Select at least two text frames.")
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
    return entries, combine_payloads(payloads)


class OrderedDocumentLink(_LinkDocumentFrames):
    def __init__(self, service, keys):
        super().__init__(service, keys[0], None)
        self.keys = list(keys)

    def apply(self, pages):
        if self.after is None:
            entries, payload = combine_chains([self.service.chain(key) for key in self.keys])
            self.after = assign_document_story(entries, payload)
            self.affected_page_ids = tuple(sorted({key[0] for key in self.after}))
        return super().apply(pages)


class OrderedCanvasLink(Command):
    def __init__(self, keys):
        super().__init__(merge_key=None)
        self.keys = list(keys)
        self._layer_id = keys[0][1]
        self.new_object = None
        self.replacement = None

    def apply(self, canvas):
        try:
            chains = []
            for _, _, object_id in self.keys:
                obj = story_objects(canvas, [object_id])[0][1]
                story = story_of(obj)
                ids = [entry["id"] for entry in story["frames"]] if story else [object_id]
                chains.append([(str(o.id), o) for _, o in story_objects(canvas, ids)])
            entries, payload = combine_chains(chains)
            actual = story_objects(canvas, [key for key, _ in entries])
            self.replacement = StoryReplacement(assign_story(actual, payload))
            return self.replacement.apply(canvas)
        except ValueError:
            return False

    def revert(self, canvas):
        return self.replacement.revert(canvas) if self.replacement else True
