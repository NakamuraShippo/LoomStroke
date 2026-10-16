"""Color state management for LoomStroke core.

This module centralises color handling so that plugins and tools can
share a single source of truth for the active painting color and related
metadata. The implementation follows the guidelines in Color_plugin_project.md
and adheres to Master.md hard constraints (type hints, logging, asyncio safety).
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

RGBA = tuple[float, float, float, float]

COLOR_HISTORY_MAX = 50
ICC_PROFILE_SRGB = "sRGB IEC61966-2.1"


@dataclass(frozen=True)
class ColorDescriptor:
    """Normalized RGBA color descriptor with optional metadata."""

    rgba: RGBA
    blend_mode: str = "normal"
    meta: Mapping[str, Any] | None = None

    def clamp(self) -> ColorDescriptor:
        """Return a clamped color descriptor in 0.0-1.0 range."""
        r, g, b, a = self.rgba
        clamped = (
            max(0.0, min(1.0, float(r))),
            max(0.0, min(1.0, float(g))),
            max(0.0, min(1.0, float(b))),
            max(0.0, min(1.0, float(a))),
        )
        return ColorDescriptor(rgba=clamped, blend_mode=self.blend_mode, meta=self.meta)


@dataclass(frozen=True)
class ColorStateSnapshot:
    """Immutable snapshot for undo/redo operations."""

    current: ColorDescriptor
    previous: ColorDescriptor | None
    history: tuple[ColorDescriptor, ...]
    timestamp: float


@dataclass
class ColorState:
    """Mutable color state with bounded history."""

    current: ColorDescriptor
    previous: ColorDescriptor | None = None
    history: deque[ColorDescriptor] = field(default_factory=lambda: deque(maxlen=COLOR_HISTORY_MAX))
    max_history: int = COLOR_HISTORY_MAX

    def apply(self, color: ColorDescriptor) -> ColorStateSnapshot:
        """Apply a new color and return a snapshot representing the change."""
        normalized = color.clamp()
        if self.max_history != self.history.maxlen:
            self.history = deque(self.history, maxlen=self.max_history)
        if self.current:
            self.previous = self.current
            self.history.appendleft(self.current)
        self.current = normalized
        snapshot = self.as_snapshot()
        return snapshot

    def revert(self, snapshot: ColorStateSnapshot) -> None:
        """Revert internal state to the provided snapshot."""
        self.current = snapshot.current
        self.previous = snapshot.previous
        self.history = deque(snapshot.history, maxlen=self.max_history)

    def as_snapshot(self) -> ColorStateSnapshot:
        """Create a snapshot of the current state."""
        return ColorStateSnapshot(
            current=self.current,
            previous=self.previous,
            history=tuple(self.history),
            timestamp=time.monotonic(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise the state for persistence."""
        return {
            "current": _color_to_dict(self.current),
            "previous": _color_to_dict(self.previous) if self.previous else None,
            "history": [_color_to_dict(c) for c in self.history],
            "max_history": self.max_history,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ColorState:
        """Create a ColorState from persisted data."""
        max_history = int(data.get("max_history", COLOR_HISTORY_MAX))
        current = _color_from_dict(data.get("current")) or ColorDescriptor((0.0, 0.0, 0.0, 1.0))
        previous = _color_from_dict(data.get("previous"))
        history_items = [_color_from_dict(item) for item in data.get("history", [])]
        history = deque([c for c in history_items if c], maxlen=max_history)
        return cls(current=current, previous=previous, history=history, max_history=max_history)


def srgb_to_linear(channel: float) -> float:
    """Convert an sRGB component to linear space."""
    c = max(0.0, min(1.0, channel))
    if c <= 0.04045:
        return float(c / 12.92)
    return float(((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(channel: float) -> float:
    """Convert a linear component to sRGB space."""
    c = max(0.0, min(1.0, channel))
    if c <= 0.0031308:
        return float(c * 12.92)
    return float(1.055 * math.pow(c, 1 / 2.4) - 0.055)


def rgba_to_hex(rgba: RGBA) -> str:
    """Convert normalized RGBA to HEX string (#RRGGBBAA)."""
    r, g, b, a = rgba
    return "#" + "".join(f"{int(x * 255):02X}" for x in (r, g, b, a))


def hex_to_rgba(value: str) -> RGBA:
    """Convert HEX string (#RGB, #RGBA, #RRGGBB, #RRGGBBAA) to normalized RGBA."""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text) + "FF"
    elif len(text) == 4:
        text = "".join(ch * 2 for ch in text)
    elif len(text) == 6:
        text += "FF"
    elif len(text) != 8:
        raise ValueError(f"Unsupported HEX color length: {value}")
    r = int(text[0:2], 16) / 255
    g = int(text[2:4], 16) / 255
    b = int(text[4:6], 16) / 255
    a = int(text[6:8], 16) / 255
    return (r, g, b, a)


def _color_to_dict(descriptor: ColorDescriptor | None) -> dict[str, Any] | None:
    if descriptor is None:
        return None
    return {
        "rgba": list(descriptor.rgba),
        "blend_mode": descriptor.blend_mode,
        "meta": dict(descriptor.meta) if descriptor.meta else None,
    }


def _color_from_dict(data: Mapping[str, Any] | None) -> ColorDescriptor | None:
    if not data:
        return None
    rgba_raw = data.get("rgba")
    if rgba_raw is None:
        return None
    rgba_tuple = tuple(float(component) for component in rgba_raw)
    if len(rgba_tuple) == 3:
        rgba_tuple = (*rgba_tuple, 1.0)
    elif len(rgba_tuple) != 4:
        raise ValueError("rgba must have 3 or 4 components")
    blend_mode = str(data.get("blend_mode", "normal"))
    meta = data.get("meta")
    return ColorDescriptor(rgba=rgba_tuple, blend_mode=blend_mode, meta=meta)


__all__ = [
    "COLOR_HISTORY_MAX",
    "ICC_PROFILE_SRGB",
    "ColorDescriptor",
    "ColorState",
    "ColorStateSnapshot",
    "hex_to_rgba",
    "linear_to_srgb",
    "rgba_to_hex",
    "srgb_to_linear",
]
