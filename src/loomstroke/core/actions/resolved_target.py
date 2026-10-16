"""Concrete ids a selector resolved to on one page, or why it could not."""

from __future__ import annotations

from dataclasses import dataclass

from .action_descriptor import TargetKind

__all__ = ["ResolvedTarget"]


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    kind: TargetKind
    ids: tuple[str, ...] = ()
    blocked_code: str = ""
    candidates: tuple[str, ...] = ()
    """Names listed when a selector is ambiguous (CT-20 §14)."""

    @property
    def ok(self) -> bool:
        return not self.blocked_code

    @property
    def single_id(self) -> str | None:
        return self.ids[0] if len(self.ids) == 1 else None

    @classmethod
    def blocked(
        cls, kind: TargetKind, code: str, candidates: tuple[str, ...] = ()
    ) -> ResolvedTarget:
        return cls(kind, (), code, candidates)
