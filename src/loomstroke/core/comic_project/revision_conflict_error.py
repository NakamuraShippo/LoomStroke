"""Raised when a save races an older snapshot (CT-17 §13: no last-writer-wins)."""

from __future__ import annotations

__all__ = ["RevisionConflictError"]


class RevisionConflictError(RuntimeError):
    def __init__(self, project_id: str, *, expected: int, actual: int) -> None:
        super().__init__(f"revision conflict on {project_id}: expected {expected}, stored {actual}")
        self.project_id = project_id
        self.expected = expected
        self.actual = actual
