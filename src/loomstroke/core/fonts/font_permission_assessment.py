"""Embedding / packaging facts for one font face (CT-25 ``FontPermissionAssessment``).

``unknown`` is never promoted to ``allowed`` — the user must decide
explicitly (spec 25 §データモデル / §エラー).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["FontPermissionAssessment"]

Embedding = Literal["installable", "editable", "preview_print", "restricted", "unknown"]
PackageCopy = Literal["allowed", "denied", "unknown"]


@dataclass(frozen=True, slots=True)
class FontPermissionAssessment:
    embedding: Embedding = "unknown"
    no_subsetting: bool = False
    bitmap_only: bool = False
    package_copy: PackageCopy = "unknown"
    evidence_codes: tuple[str, ...] = ()

    @property
    def may_embed_subset(self) -> bool:
        return (
            self.embedding in ("installable", "editable", "preview_print")
            and not self.no_subsetting
        )

    @property
    def may_embed(self) -> bool:
        return self.embedding in ("installable", "editable", "preview_print")
