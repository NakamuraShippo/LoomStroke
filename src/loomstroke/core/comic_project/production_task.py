"""Production task on a page / panel / dialogue (CT-17 §7.1).

``assignee_label`` is a free label (never an account identity); it is
excluded from every projection (spec 17 §12).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

__all__ = ["ProductionTask"]


@dataclass(frozen=True, slots=True)
class ProductionTask:
    task_id: str
    target_ref: str
    stage: str
    state: str = "todo"
    assignee_label: str = ""
    due_date: str | None = None
    dependencies: tuple[str, ...] = ()

    STATES: ClassVar[tuple[str, ...]] = ("todo", "in_progress", "blocked", "done")
