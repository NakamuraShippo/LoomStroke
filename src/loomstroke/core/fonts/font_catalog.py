"""Installed / package font discovery boundary (CT-25 ``FontDiscoveryAdapter``).

Core asks questions; the Qt adapter answers from ``QFontDatabase``, tests
answer from a dict.  No font files or paths cross this boundary.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

__all__ = ["FontCatalog"]


class FontCatalog(Protocol):
    def families(self) -> Sequence[str]: ...

    def styles(self, family: str) -> Sequence[str]: ...

    def has_family(self, family: str) -> bool: ...

    def missing_glyphs(self, family: str, characters: Iterable[str]) -> int:
        """Number of ``characters`` the family cannot render (0 when unknown/complete)."""
        ...
