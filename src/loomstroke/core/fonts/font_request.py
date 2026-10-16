"""What a text style *asks for* (CT-25 spec §データモデル ``FontRequest``).

The requested identity is the truth kept on the document; runtime
fallback never rewrites it (spec 25 §原文 / font指定保持).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["FontRequest"]


@dataclass(frozen=True, slots=True)
class FontRequest:
    family: str
    style: str | None = None
    weight: int = 400
    stretch: int = 100
    variation_axes: tuple[tuple[str, float], ...] = ()

    @property
    def key(self) -> str:
        """Stable grouping key (family + style, case-insensitive)."""
        return f"{self.family.strip().casefold()}|{(self.style or '').strip().casefold()}"

    @property
    def label(self) -> str:
        return f"{self.family} {self.style}".strip() if self.style else self.family
