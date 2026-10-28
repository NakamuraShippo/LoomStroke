"""Call-local evidence for history restoration; never retains a Command payload."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EntryRestoreOutcome:
    restored: bool
    primary_confirmed: bool = False


def legacy_restore_outcome(result: Any) -> EntryRestoreOutcome:
    """Keep a custom bool boundary without inventing primary-completion proof."""
    return EntryRestoreOutcome(bool(result))


def uses_default_restore_method(method: Any, implementation: Any) -> bool:
    """Instance hooks/subclass overrides retain their existing bool contract."""
    return getattr(method, "__func__", None) is implementation
