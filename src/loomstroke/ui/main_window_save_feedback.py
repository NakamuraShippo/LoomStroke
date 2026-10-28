"""Feedback state scoped to one manual-save attempt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ManualSaveFeedback:
    target: Path
    deferred_notice_shown: bool = False

    def matches_path(self, path: Path | None) -> bool:
        """Match the built-in writer's existing .lstroke suffix normalization."""
        target = self.target
        if target.suffix.lower() != ".lstroke":
            target = target.with_suffix(".lstroke")
        return path == target
