"""Refresh derived frame geometry after command-backed vector membership changes."""

from copy import deepcopy
from dataclasses import replace

from .linked_story import frame_entries, story_of


def layer_story_objects(layer):
    result = list(getattr(layer, "vector_objects", []) or [])
    for child in getattr(layer, "children", []) or []:
        result.extend(layer_story_objects(child))
    return result


def refresh_story_geometry(canvas, changed):
    service = getattr(canvas, "_document_story_service", None)
    if service is not None:
        service.refresh_geometry(changed)
    changed = [obj for obj in changed if (story_of(obj) or {}).get("scope") != "document"]
    ids = {story["id"] for obj in changed if (story := story_of(obj)) and "id" in story}
    getter = getattr(canvas, "get_vector_objects", None)
    if not ids or not callable(getter):
        return False
    changed_any = False
    entries = getter(include_hidden=True)
    for story_id in ids:
        members = [
            (layer_id, obj)
            for layer_id, obj in entries
            if (story := story_of(obj)) and story.get("id") == story_id
        ]
        if not members:
            continue
        seed = max((story_of(obj) for _, obj in members), key=lambda value: len(value["frames"]))
        order = [entry["id"] for entry in seed["frames"]]
        members.sort(
            key=lambda entry: (
                order.index(str(entry[1].id)) if str(entry[1].id) in order else len(order)
            )
        )
        frames = frame_entries(members)
        for layer_id, obj in members:
            if story_of(obj)["frames"] == frames:
                continue
            meta = deepcopy(obj.meta)
            meta["text_tool"]["linked_story"]["frames"] = deepcopy(frames)
            layer = canvas._get_layer_by_id(layer_id)
            for index, current in enumerate(layer.vector_objects):
                if current.id == obj.id:
                    layer.vector_objects[index] = replace(current, meta=meta)
                    canvas._qpath_cache.pop(id(current), None)
                    layer.texture_version += 1
                    changed_any = True
                    break
    return changed_any
