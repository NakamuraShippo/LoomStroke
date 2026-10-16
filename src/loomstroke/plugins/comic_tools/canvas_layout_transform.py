"""Reorient the editable panel split tree with a whole-canvas transform."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .layout_geometry_space import reorient_space, translated_space
from .layout_tree import LAYOUT_SCHEMA_VERSION, layout_from_payload
from .model import LAYOUT_LAYER_NAMESPACE

_MODES = {"flip_h", "flip_v", "flip_hv", "rot90", "rot-90", "rot180"}


def transform_layout_metadata(
    metadata: Mapping[str, Any], mode: str, old_width: int, old_height: int
) -> dict[str, Any] | None:
    """Copy-on-write transform, preserving ids, reading order and extensions.

    Panel widths are relative weights; right-angle transforms need only
    swap split axes and/or reverse children together with their weights.
    Pixel gutter and margin remain unchanged. Unknown schemas are rejected
    before canvas mutation rather than silently damaging an editable layout.
    """
    plugins = metadata.get("plugins")
    if not isinstance(plugins, Mapping) or LAYOUT_LAYER_NAMESPACE not in plugins:
        return None
    if mode not in _MODES and not mode.startswith("translate:"):
        return None
    payload = plugins[LAYOUT_LAYER_NAMESPACE]
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version", 1) != LAYOUT_SCHEMA_VERSION
        or layout_from_payload(payload) is None
    ):
        raise ValueError("Cannot transform an unsupported comic panel layout")
    result = copy.deepcopy(dict(metadata))
    changed = result["plugins"][LAYOUT_LAYER_NAMESPACE]
    if mode.startswith("translate:"):
        _, dx, dy = mode.split(":")
        changed["metadata"] = translated_space(
            changed.get("metadata", {}), (old_width, old_height), float(dx), float(dy)
        )
        return result
    changed["metadata"] = reorient_space(changed.get("metadata", {}), (old_width, old_height), mode)
    tree = changed["tree"]
    _transform_node(tree, mode)
    return result


def _transform_node(node: dict[str, Any], mode: str) -> None:
    if node.get("panel_id"):
        return
    orientation = node["orientation"]
    children = node["children"]
    for child in children:
        _transform_node(child, mode)
    reverse = (
        mode in {"flip_hv", "rot180"}
        or (mode in {"flip_h", "rot-90"} and orientation == "vertical")
        or (mode in {"flip_v", "rot90"} and orientation == "horizontal")
    )
    if reverse:
        node["children"] = list(reversed(children))
        # Readers accept omitted/mismatched weights as equal shares.
        weights = node.get("weights", [])
        if len(weights) == len(children):
            node["weights"] = list(reversed(weights))
    if "cut_skews" in node:
        skews = node["cut_skews"]
        if reverse:
            skews = list(reversed(skews))
        if mode in {"flip_h", "flip_v", "rot90", "rot-90"}:
            skews = [-value for value in skews]
        node["cut_skews"] = skews
    if mode in {"rot90", "rot-90"}:
        node["orientation"] = "horizontal" if orientation == "vertical" else "vertical"
