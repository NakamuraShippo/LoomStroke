"""Outcome of a point-level path correction: new points plus which inputs survived."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PathCorrectionResult"]

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class PathCorrectionResult:
    points: tuple[Point, ...]
    kept_indices: tuple[int, ...] = ()
    """Input indexes that map 1:1 onto ``points`` (stable ids follow them)."""
    issues: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def changed(self) -> bool:
        return len(self.kept_indices) != len(self.points) or self._moved

    @property
    def _moved(self) -> bool:
        return bool(getattr(self, "_moved_flag", False))
