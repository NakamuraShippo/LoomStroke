"""Dialogue -> canvas plans (CT-17 §6 ``DialogueApplyPlanner``).

Pure: decides *what* a text object should contain and where it goes
(direction from the dialogue, a default frame near the page's upper
area scaled to the page size).  Submitting the plan through the canvas
Command path is the UI bridge's job; the link is only committed after
that command succeeds (spec 17 §8).
"""

from __future__ import annotations

from .dialogue_apply_plan import DialogueApplyPlan
from .dialogue_entry import DialogueEntry
from .dialogue_sync_plan import DialogueSyncPlan, SyncDirection

__all__ = ["DialogueApplyPlanner"]


class DialogueApplyPlanner:
    DEFAULT_FONT_PT: float = 24.0

    def plan_create_text(
        self,
        dialogue: DialogueEntry,
        *,
        page_id: str,
        page_size: tuple[int, int],
        rect: tuple[float, float, float, float] | None = None,
        vertical: bool | None = None,
        font_size_pt: float | None = None,
    ) -> DialogueApplyPlan:
        width, height = max(1, int(page_size[0])), max(1, int(page_size[1]))
        is_vertical = (
            dialogue.reading_direction == "vertical" if vertical is None else bool(vertical)
        )
        if rect is None:
            # A balloon-sized frame in the upper third, right side for vertical
            # (manga reads right to left), left side otherwise.
            frame_w = max(48.0, width * (0.18 if is_vertical else 0.32))
            frame_h = max(48.0, height * (0.30 if is_vertical else 0.14))
            x = width - frame_w - width * 0.06 if is_vertical else width * 0.06
            y = height * 0.08
            rect = (float(x), float(y), float(frame_w), float(frame_h))
        return DialogueApplyPlan(
            dialogue_id=dialogue.dialogue_id,
            page_id=page_id,
            content=dialogue.text,
            rect=rect,
            vertical=is_vertical,
            font_size_pt=float(font_size_pt or self.DEFAULT_FONT_PT),
        )

    @staticmethod
    def plan_sync(
        dialogue: DialogueEntry,
        *,
        page_id: str,
        object_id: str,
        canvas_text: str,
        direction: SyncDirection,
    ) -> DialogueSyncPlan:
        return DialogueSyncPlan(
            dialogue_id=dialogue.dialogue_id,
            page_id=page_id,
            object_id=object_id,
            direction=direction,
            script_text=dialogue.text,
            canvas_text=canvas_text,
        )
