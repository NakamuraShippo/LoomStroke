"""Explicit font substitution (CT-25 F2 ``FontSubstitutionPlan``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .font_request import FontRequest
from .font_usage import ObjectRef

__all__ = ["FontSubstitutionPlan"]

Scope = Literal["document", "page", "selection"]


@dataclass(frozen=True, slots=True)
class FontSubstitutionPlan:
    source: FontRequest
    target: FontRequest
    scope: Scope
    object_refs: tuple[ObjectRef, ...]
    keep_original_request: bool = True

    @property
    def is_empty(self) -> bool:
        return not self.object_refs

    def refs_for_page(self, page_id: str) -> tuple[ObjectRef, ...]:
        return tuple(ref for ref in self.object_refs if ref[0] == page_id)
