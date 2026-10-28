"""Atomic vector replacements across live and hydrated document pages."""

from copy import deepcopy

from .page_mutation_preflight import PageMutationPreflight


class _VectorBatchSuperseded(ValueError):
    """The original document or page state no longer owns this batch."""

    def __init__(self):
        super().__init__("The document changed during the vector update.")


def _vector_batch_scope(pages, page_ids):
    # Missing original targets still use the existing full-key validation.
    # They must not be mistaken for a document replacement.
    existing = {state.page.id for state in pages._pages}
    return PageMutationPreflight(
        pages, tuple(page_id for page_id in page_ids if page_id in existing)
    )


def _require_vector_scope(scope):
    if not scope.is_current():
        raise _VectorBatchSuperseded()


def _call_vector_scope(scope, callback, *args, **kwargs):
    _require_vector_scope(scope)
    try:
        result = callback(*args, **kwargs)
    except Exception:
        _require_vector_scope(scope)
        raise
    _require_vector_scope(scope)
    return result


def vector_entries(pages, *, page_ids=None):
    """Read current objects without switching the visible page or its history."""
    result = {}
    for summary in pages.list_pages():
        page_id = summary.id
        if page_ids is not None and page_id not in page_ids:
            continue
        if page_id == pages.get_active_page_id():
            layers = pages._canvas._layers
        else:
            snapshot = pages.get_page_snapshot(page_id)
            if snapshot is None:
                raise ValueError("A page could not be loaded.")
            layers = snapshot.layers
        stack = [(layer, False) for layer in layers]
        while stack:
            layer, parent_locked = stack.pop()
            locked = parent_locked or bool(getattr(layer, "locked", False))
            stack.extend((child, locked) for child in getattr(layer, "children", []) or [])
            for index, obj in enumerate(getattr(layer, "vector_objects", []) or []):
                if getattr(obj, "id", None):
                    key = (page_id, str(layer.id), str(obj.id))
                    if key in result:
                        raise ValueError("Duplicate vector object identifiers.")
                    result[key] = (layer, index, obj, locked)
    return result


def replace_vector_objects(pages, replacements, *, allow_locked=False):
    """Validate the entire batch before changing any object, then rollback on failure."""
    if not replacements:
        return {}
    lease = pages._hold_page_residency()
    try:
        return _replace_resident_vector_objects(pages, replacements, allow_locked=allow_locked)
    finally:
        # The lease belongs to the original document's guard. A callback
        # loading another document cannot make this release its new token.
        lease.release()


def _replace_resident_vector_objects(pages, replacements, *, allow_locked):
    page_ids = {key[0] for key in replacements}
    scope = _vector_batch_scope(pages, page_ids)
    entries = _call_vector_scope(scope, vector_entries, pages, page_ids=page_ids)
    previous = {}
    for key, obj in replacements.items():
        entry = entries.get(key)
        if entry is None or (entry[3] and not allow_locked):
            raise ValueError("A vector object is missing or locked.")
        if str(obj.id) != key[2]:
            raise ValueError("A vector replacement changed its identifier.")
        previous[key] = deepcopy(entry[2])
    changed = []
    try:
        for key, obj in replacements.items():
            _require_vector_scope(scope)
            _replace(pages, key, entries[key], obj)
            _require_vector_scope(scope)
            changed.append(key)
    except Exception:
        # A replacement callback may have opened another document, even if
        # it subsequently raises. Never run old inverse edits on its live ids.
        _require_vector_scope(scope)
        for key in reversed(changed):
            _replace(pages, key, entries[key], previous[key])
            _require_vector_scope(scope)
        if any(key[0] == pages.get_active_page_id() for key in changed):
            _call_vector_scope(scope, pages._canvas._recompose)
        raise
    for page_id in page_ids:
        _require_vector_scope(scope)
        state = pages._find_page_state(page_id)
        state.dirty = True
        _call_vector_scope(scope, pages._emit_page_dirty_changed, page_id, True)
    if replacements:
        if any(key[0] == pages.get_active_page_id() for key in replacements):
            _call_vector_scope(scope, pages._canvas._recompose)
        _call_vector_scope(scope, pages._emit_document_changed, "text.styles_changed")
    return previous


def _replace(pages, key, entry, obj):
    page_id, layer_id, object_id = key
    layer, index, _, _ = entry
    if page_id == pages.get_active_page_id():
        replacement = deepcopy(obj)
        result = pages._canvas.replace_vector_object(
            object_id, replacement, layer_id=layer_id, recompose=False
        )
        if result is None:
            raise ValueError("The vector object could not be updated.")
        if "layer_id" not in obj.meta:
            replacement.meta.pop("layer_id", None)
        layer.texture_version += 1
    else:
        layer.vector_objects[index] = deepcopy(obj)
