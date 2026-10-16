"""Result of executing one action against one target."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ActionOutcome"]


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    ok: bool
    reason_code: str = ""
    detail: str = ""
    outputs: tuple[str, ...] = ()

    @classmethod
    def success(cls, *outputs: str) -> ActionOutcome:
        return cls(True, outputs=tuple(outputs))

    @classmethod
    def failure(cls, reason_code: str, detail: str = "") -> ActionOutcome:
        return cls(False, reason_code=reason_code, detail=detail)
