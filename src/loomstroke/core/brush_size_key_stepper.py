"""Proportional, accelerating step policy for the ``[`` / ``]`` brush size keys.

Each press scales the size by a ratio instead of a fixed pixel amount, so
small and large brushes feel equally responsive. Presses arriving within
``REPEAT_WINDOW_SEC`` in the same direction (rapid taps or OS key repeat)
grow the ratio up to the cap of the configured acceleration level.
"""

from __future__ import annotations

import math

BASE_RATIO = 0.10
RATIO_INCREMENT = 0.03
REPEAT_WINDOW_SEC = 0.25
MIN_STEP_PX = 1.0

# Stable setting values -> maximum per-press ratio. "none" rather than "off":
# SettingsManager reads the string "off" back as boolean False.
ACCELERATION_LEVELS: dict[str, float] = {
    "none": BASE_RATIO,
    "weak": 0.15,
    "standard": 0.25,
    "strong": 0.40,
}
DEFAULT_ACCELERATION_LEVEL = "standard"
ACCELERATION_SETTING_KEY = "input/brush_size_key_acceleration"

__all__ = [
    "ACCELERATION_LEVELS",
    "ACCELERATION_SETTING_KEY",
    "DEFAULT_ACCELERATION_LEVEL",
    "BrushSizeKeyStepper",
    "normalize_acceleration_level",
]


def normalize_acceleration_level(value: object) -> str:
    """Return a known level name, falling back to the default."""
    if isinstance(value, str):
        token = value.strip().lower()
        if token in ACCELERATION_LEVELS:
            return token
    return DEFAULT_ACCELERATION_LEVEL


class BrushSizeKeyStepper:
    """Computes the next brush size for one key press and tracks repeat state."""

    def __init__(self, level: str = DEFAULT_ACCELERATION_LEVEL) -> None:
        self._max_ratio = ACCELERATION_LEVELS[normalize_acceleration_level(level)]
        self._last_time: float | None = None
        self._last_direction = 0
        self._streak = 0

    def set_level(self, level: object) -> None:
        self._max_ratio = ACCELERATION_LEVELS[normalize_acceleration_level(level)]
        self.reset()

    def reset(self) -> None:
        self._last_time = None
        self._last_direction = 0
        self._streak = 0

    def next_size(
        self,
        current: float,
        direction: int,
        *,
        now: float,
        min_value: float,
        max_value: float,
    ) -> float:
        """Return the size after one press; ``direction`` is +1 or -1."""
        direction = 1 if direction > 0 else -1
        repeating = (
            self._last_time is not None
            and direction == self._last_direction
            and 0.0 <= now - self._last_time <= REPEAT_WINDOW_SEC
        )
        self._streak = self._streak + 1 if repeating else 0
        self._last_time = now
        self._last_direction = direction

        ratio = min(self._max_ratio, BASE_RATIO + RATIO_INCREMENT * self._streak)
        if direction > 0:
            target = max(current * (1.0 + ratio), current + MIN_STEP_PX)
            target = float(math.floor(target + 0.5))
            # A size already above the range is never pulled down by "increase".
            return max(current, min(target, max_value))
        target = min(current / (1.0 + ratio), current - MIN_STEP_PX)
        target = float(math.floor(target + 0.5))
        return max(min_value, min(target, current))
