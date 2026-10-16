"""Requested identity -> resolved identity (CT-25 §アーキテクチャ).

``status``: ``exact`` (family + style found), ``family_only`` (family
found, style substituted by the renderer), ``fallback`` (family missing;
the renderer will substitute — the request is kept untouched), or
``missing`` when no catalog was consulted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .font_identity import FontIdentity
from .font_request import FontRequest

__all__ = ["FontResolutionResult"]

ResolutionStatus = Literal["exact", "family_only", "fallback", "missing"]


@dataclass(frozen=True, slots=True)
class FontResolutionResult:
    request: FontRequest
    status: ResolutionStatus
    resolved_family: str = ""
    resolved_style: str = ""
    identity: FontIdentity | None = None
    missing_glyphs: int = 0

    @property
    def is_available(self) -> bool:
        return self.status in ("exact", "family_only")

    @property
    def uses_fallback(self) -> bool:
        return self.status == "fallback"
