"""What an action does to the world, for dry-run reports (CT-20 §3 / §6.2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["ActionEffect", "EffectKind"]

EffectKind = Literal["mutation", "file_write", "external", "read_only"]


@dataclass(frozen=True, slots=True)
class ActionEffect:
    kind: EffectKind
    description: str = ""
    confirmation_required: bool = False

    @property
    def is_side_effect(self) -> bool:
        return self.kind in {"file_write", "external"}
