"""Hide paint-medium settings that the selected medium does not use."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Each medium shows only what its brush does: a wash has water, runs dry and lifts;
# oil picks up the colour below and returns to its own.
_NO_WATER = frozenset({"water_amount", "paint_remaining", "paint_lifting"})
_NO_OIL = frozenset({"paint_pickup", "paint_recovery"})
_MEDIUM_ONLY = _NO_WATER | _NO_OIL | {"paper", "paint_load", "paint_reach"}
# The eraser's kneaded type lifts with a lump: no stamp spacing or edge smoothing, and
# only it gets dirty.
_NO_KNEADED = frozenset({"durability"})
_KNEADED_UNUSED = frozenset({"spacing", "anti_alias"})
# The setting of each tool that decides which of its other fields are shown.
SWITCH_KEYS = {"basic_pen": "medium", "basic_eraser": "eraser_type"}


def hidden_medium_keys(tool_id: str, settings: Mapping[str, Any]) -> frozenset[str]:
    if tool_id == "basic_eraser":
        return _KNEADED_UNUSED if settings.get("eraser_type") == "Kneaded" else _NO_KNEADED
    medium = settings.get("medium")
    if tool_id != "basic_pen":
        return frozenset()
    if medium == "Oil":
        return _NO_WATER
    return _NO_OIL if medium == "Watercolor" else _MEDIUM_ONLY
