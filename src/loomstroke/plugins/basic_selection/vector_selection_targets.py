"""Resolve a selection with layer/object keys, retaining unique legacy IDs."""

from __future__ import annotations

from typing import Any


def resolve_vector_selection_targets(
    store: list[tuple[str | None, Any]], items: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], str | None, Any]]:
    exact: dict[tuple[str | None, str], tuple[str | None, Any]] = {}
    unique: dict[str, tuple[str | None, Any] | None] = {}
    for layer_id, obj in store:
        object_id = getattr(obj, "id", None)
        if not isinstance(object_id, str):
            continue
        entry = (layer_id, obj)
        exact[(layer_id, object_id)] = entry
        unique[object_id] = None if object_id in unique else entry
    resolved: list[tuple[dict[str, Any], str | None, Any]] = []
    for item in items:
        object_id = item.get("id")
        if not isinstance(object_id, str):
            continue
        layer_id = item.get("layer_id")
        entry = (
            exact.get((layer_id, object_id)) if isinstance(layer_id, str) else unique.get(object_id)
        )
        if entry is not None:
            resolved.append((item, entry[0], entry[1]))
    return resolved
