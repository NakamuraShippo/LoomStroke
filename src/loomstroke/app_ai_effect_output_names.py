"""Allocate AI output names from ComfyUI titles or the source layer."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def output_layer_names(
    canvas: Any, layer_id: str, *, state: Any = None
) -> Callable[[str], str] | None:
    """Snapshot names once per generation, including AI layers inside groups.

    Hosts without layer lookup retain their existing manifest-name fallback.
    Numbering has no persistent counter; saved layer names provide collisions.
    """
    find_layer = getattr(canvas, "_find_layer_by_id_tree", None)
    find_parent = getattr(canvas, "_find_parent", None)
    if not callable(find_layer) or not callable(find_parent):
        return None
    source = find_layer(layer_id)
    parent = find_parent(layer_id)
    if source is None or parent is None:
        return None
    source_name = getattr(source, "name", None)
    if not isinstance(source_name, str):
        return None
    siblings, _index = parent
    used_names = {layer.name for layer in siblings}
    titles = _custom_output_titles(state)
    serials: dict[str, int] = {}

    def allocate(internal_id: str) -> str:
        title = titles.get(internal_id)
        if title is not None and title not in used_names:
            used_names.add(title)
            return title
        base = title if title is not None else source_name
        serial = serials.get(base, 1)
        while f"{base} {serial}" in used_names:
            serial += 1
        name = f"{base} {serial}"
        serials[base] = serial + 1
        used_names.add(name)
        return name

    return allocate


def _custom_output_titles(state: Any) -> dict[str, str]:
    if getattr(state, "gateway_id", None) != "comfyui":
        return {}
    snapshot = getattr(state, "preset_snapshot", None)
    gateway_file = snapshot.get("gateway_file") if isinstance(snapshot, dict) else None
    if not isinstance(gateway_file, dict):
        return {}
    workflow = gateway_file.get("workflow")
    mappings = gateway_file.get("node_mappings")
    if not isinstance(workflow, dict) or not isinstance(mappings, dict):
        return {}
    titles: dict[str, str] = {}
    for internal_id, ref in mappings.items():
        if not isinstance(ref, dict):
            continue
        node = workflow.get(str(ref.get("node_id")))
        if not isinstance(node, dict):
            continue
        meta = node.get("_meta")
        title = meta.get("title") if isinstance(meta, dict) else None
        if not isinstance(title, str) or not title.strip():
            continue
        title = title.strip()
        class_type = str(node.get("class_type", ""))
        # Built-in display titles insert spaces into the class name.
        if "".join(title.split()).casefold() == "".join(class_type.split()).casefold():
            continue
        titles[internal_id] = title
    return titles
