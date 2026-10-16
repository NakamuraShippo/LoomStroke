"""Optional per-action dry-run detail (outputs, warnings, blocking) from a planner adapter."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ActionPreview"]


@dataclass(frozen=True, slots=True)
class ActionPreview:
    warnings: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    """Basenames only; never absolute paths (CT-20 §13)."""
    blocked_code: str = ""

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocked_code)
