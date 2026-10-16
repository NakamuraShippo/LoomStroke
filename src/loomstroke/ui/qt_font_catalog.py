"""``FontCatalog`` over ``QFontDatabase`` (CT-25 platform ``FontDiscoveryAdapter``)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics

__all__ = ["QtFontCatalog"]


class QtFontCatalog:
    def families(self) -> Sequence[str]:
        try:
            return tuple(sorted(QFontDatabase.families()))
        except Exception:
            return ()

    def styles(self, family: str) -> Sequence[str]:
        try:
            return tuple(QFontDatabase.styles(family))
        except Exception:
            return ()

    def has_family(self, family: str) -> bool:
        try:
            return bool(QFontDatabase.hasFamily(family))
        except Exception:
            return family in self.families()

    def missing_glyphs(self, family: str, characters: Iterable[str]) -> int:
        try:
            metrics = QFontMetrics(QFont(family))
            return sum(1 for ch in characters if ch and not metrics.inFontUcs4(ord(ch)))
        except Exception:
            return 0
