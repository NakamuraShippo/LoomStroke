"""Prepare dialogue edits without navigating pages or changing live artwork."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

from loomstroke.core.japanese_text.speaker_styles import apply_style, resolve_style
from loomstroke.plugins.vector_tools.common import ensure_vector_element_ids

from .dialogue import _iter_vector_objects, rows_for_page
from .service import ComicPageController


class DialogueBatch:
    def __init__(self, tool, keys):
        self.tool = tool
        self.rows, self.sources = [], {}
        pages = tool.host_api.pages
        if not callable(getattr(pages, "apply_vector_batch", None)):
            raise ValueError("Document batch editing is unavailable.")
        pages.persist_active_page()
        selected = list(dict.fromkeys(keys))
        summaries = {str(page.id): page for page in pages.list_pages()}
        page_sources = {}
        for page_id, balloon_id in selected:
            if page_id not in page_sources:
                snapshot = pages.get_page_snapshot(page_id)
                if snapshot is None:
                    raise ValueError("The dialogue page is unavailable.")
                controller = ComicPageController(page_key=page_id)
                # Copy editable vectors, not the page's raster images. Scan each page once.
                state = deepcopy(
                    controller.read_state(
                        SimpleNamespace(_layers=snapshot.layers), page_size=(1, 1)
                    )
                )
                objects = {
                    str(obj.id): deepcopy(obj) for obj in _iter_vector_objects(snapshot.layers)
                }
                rows = rows_for_page(
                    snapshot.layers, page_id=page_id, page_name=summaries[page_id].name or ""
                )
                page_sources[page_id] = (
                    controller,
                    state,
                    objects,
                    {row.balloon_id: row for row in rows},
                )
            controller, state, objects, rows = page_sources[page_id]
            row = rows.get(balloon_id)
            if row is None or balloon_id not in state.text_objects:
                raise ValueError("A selected dialogue no longer exists.")
            self.rows.append(row)
            self.sources[(page_id, balloon_id)] = (controller, state, objects)

    def commit(self, changes):
        if [(row["page_id"], row["balloon_id"]) for row in changes] != list(self.sources):
            raise ValueError("The selected dialogue rows changed.")
        pages = self.tool.host_api.pages
        registry = pages.speaker_styles.read()
        updates, expected = {}, {}
        for original, change in zip(self.rows, changes, strict=True):
            if (change["text"], change["speaker_id"], change["speaker_name"]) == (
                original.text,
                original.speaker_id,
                original.speaker_name,
            ):
                continue
            controller, state, objects = self.sources[(original.page_id, original.balloon_id)]
            record = replace(
                state.balloons[original.balloon_id],
                speaker_id=change["speaker_id"],
                speaker_name=change["speaker_name"],
            )
            obj = state.text_objects[original.balloon_id]
            if (record.speaker_id, record.speaker_name) != (
                original.speaker_id,
                original.speaker_name,
            ):
                style = resolve_style(registry, record.speaker_id, record.speaker_name)
                payload = deepcopy(obj.meta["text_tool"])
                if style is not None:
                    payload = apply_style(payload, style)
                else:
                    payload.pop("speaker_style_ref", None)
                obj = replace(obj, meta={**deepcopy(obj.meta), "text_tool": payload})
                state = replace(
                    state, text_objects={**state.text_objects, original.balloon_id: obj}
                )
            command = controller.update_balloon_command(state, record, text=change["text"])
            # Only body metadata/geometry and text change. Existing raster artwork is untouched.
            for layer_id, updated in command._replacements:
                previous = objects.get(str(updated.id))
                if previous is None:
                    raise ValueError("A dialogue object is missing.")
                key = (original.page_id, str(layer_id), str(updated.id))
                expected[key] = previous
                updates[key] = ensure_vector_element_ids(
                    replace(updated, meta={**deepcopy(previous.meta), **updated.meta})
                )
        if not updates:
            return False
        if not pages.apply_vector_batch(updates, expected):
            raise ValueError("The dialogue changed or a layer is locked. Reopen the batch editor.")
        return True
