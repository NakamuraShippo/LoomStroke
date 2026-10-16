"""Plan for reconciling script text with canvas text (CT-17 §5.2 / §13)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["DialogueSyncPlan"]

SyncDirection = Literal["script_to_canvas", "canvas_to_script"]


@dataclass(frozen=True, slots=True)
class DialogueSyncPlan:
    dialogue_id: str
    page_id: str
    object_id: str
    direction: SyncDirection
    script_text: str
    canvas_text: str

    @property
    def resulting_text(self) -> str:
        return self.script_text if self.direction == "script_to_canvas" else self.canvas_text

    @property
    def is_noop(self) -> bool:
        return self.script_text == self.canvas_text
