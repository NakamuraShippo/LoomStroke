"""Plan for creating a text object from a dialogue (CT-17 §7.2)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DialogueApplyPlan"]


@dataclass(frozen=True, slots=True)
class DialogueApplyPlan:
    dialogue_id: str
    page_id: str
    content: str
    rect: tuple[float, float, float, float]
    vertical: bool
    font_size_pt: float = 24.0
    merge_key: str = "comic_project.create_text"
