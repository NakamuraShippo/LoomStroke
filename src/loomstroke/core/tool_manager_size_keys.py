"""Host-side ``[`` / ``]`` brush size keys for tools exposing a ``size`` property.

Runs only when the active tool's ``on_key`` did not handle the key, so tools
with their own bracket behaviour keep it. Wheel notches over the tool panel's
size slider use the same stepper. The new value goes through
``ToolManager.set_tool_setting`` so the tool and its UI slider stay in sync.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from loomstroke.core.tool_manager import ToolManager

logger = logging.getLogger(__name__)

SIZE_KEY_DIRECTIONS = {"[": -1, "]": 1}
_SIZE_PROPERTY_KEY = "size"
_DEFAULT_MIN_SIZE = 1.0

__all__ = ["SIZE_KEY_DIRECTIONS", "step_active_tool_size", "step_active_tool_size_direction"]


def _size_range(manager: ToolManager, tool_id: str) -> tuple[float, float] | None:
    metadata = manager._registry.get_tool_metadata(tool_id)
    if metadata is None:
        return None
    for spec in metadata.properties:
        if spec.key != _SIZE_PROPERTY_KEY:
            continue
        min_value = _DEFAULT_MIN_SIZE if spec.min_value is None else float(spec.min_value)
        max_value = float("inf") if spec.max_value is None else float(spec.max_value)
        return min_value, max_value
    return None


def _current_size(manager: ToolManager, tool_id: str, plugin: Any) -> float | None:
    value: Any = None
    getter = getattr(plugin, "get_settings", None)
    if callable(getter):
        try:
            settings = manager._settings_to_dict(getter())
        except Exception:
            logger.debug("Tool %s get_settings failed", tool_id, exc_info=True)
        else:
            value = settings.get(_SIZE_PROPERTY_KEY)
    if value is None:
        value = manager.get_tool_setting(tool_id, _SIZE_PROPERTY_KEY)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def step_active_tool_size(manager: ToolManager, key: str) -> bool:
    """Step the active tool's size for a bracket key; return True if applied."""
    direction = SIZE_KEY_DIRECTIONS.get(key)
    if direction is None:
        return False
    return step_active_tool_size_direction(manager, direction)


def step_active_tool_size_direction(manager: ToolManager, direction: int) -> bool:
    """Step the active tool's size up (+1) or down (-1); return True if applied."""
    tool_id = manager._active_tool_id
    plugin = manager._active_tool
    if tool_id is None or plugin is None:
        return False
    size_range = _size_range(manager, tool_id)
    if size_range is None:
        return False
    current = _current_size(manager, tool_id, plugin)
    if current is None:
        return False
    min_value, max_value = size_range
    new_size = manager._size_key_stepper.next_size(
        current,
        direction,
        now=time.monotonic(),
        min_value=min_value,
        max_value=max_value,
    )
    if new_size != current:
        manager.set_tool_setting(tool_id, _SIZE_PROPERTY_KEY, new_size)
    return True
