"""Transient linked-story views; committed objects and history stay untouched."""

from copy import deepcopy
from dataclasses import replace

from .linked_story import assign_story, story_of


def preview_objects(objects, preview):
    story = story_of(preview)
    if story is None:
        return list(objects)
    result = []
    for obj in objects:
        member = story_of(obj)
        if member is None or member["id"] != story["id"] or obj.id == preview.id:
            result.append(obj)
            continue
        projected = assign_story([(None, obj)], preview.meta["text_tool"], story["id"])[0][1]
        meta = deepcopy(projected.meta)
        meta["text_tool"]["linked_story"] = {
            **deepcopy(member),
            "frames": deepcopy(story["frames"]),
        }
        result.append(replace(projected, meta=meta))
    return result


def preview_layer_ids(canvas):
    story = story_of(getattr(canvas, "_vector_preview", None))
    if story is None:
        return set()
    return {
        layer_id
        for layer_id, obj in canvas.get_vector_objects(include_hidden=True)
        if (member := story_of(obj)) and member["id"] == story["id"]
    }
