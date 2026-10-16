"""Webtoon publishing profile (CT-12 spec 12 §9 / §6.2 / §19).

A profile is a *versioned* set of output constraints.  Only the generic
LoomStroke profile ships in code; platform profiles are deliberately not
asserted here (their requirements change, spec 12 §3.7) — the UI shows
the revision / update date and asks the user to confirm the platform's
current rules before publishing.  Stable ``id`` + ``revision`` are what
may ever be persisted (spec 12 §12), never display names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

__all__ = ["WebtoonProfile"]


@dataclass(frozen=True, slots=True)
class WebtoonProfile:
    """Output constraints for one publishing target."""

    id: str
    revision: int
    target_width_px: int
    max_slice_height_px: int
    min_slice_height_px: int = 64
    max_file_bytes: int | None = None
    allowed_formats: tuple[str, ...] = ("png", "jpeg", "webp")
    updated: str = ""
    note: str = ""

    GENERIC_ID: ClassVar[str] = "loomstroke.generic"

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("profile id is required")
        if self.revision < 1:
            raise ValueError("profile revision must be >= 1")
        if self.target_width_px < 1:
            raise ValueError("target width must be positive")
        if self.min_slice_height_px < 1:
            raise ValueError("minimum slice height must be positive")
        if self.max_slice_height_px < self.min_slice_height_px:
            raise ValueError("maximum slice height must be >= the minimum slice height")
        if self.max_file_bytes is not None and self.max_file_bytes < 1:
            raise ValueError("maximum file size must be positive when set")
        if not self.allowed_formats:
            raise ValueError("at least one format must be allowed")

    @classmethod
    def generic(cls) -> WebtoonProfile:
        """Conservative defaults that fit common vertical-scroll platforms."""
        return cls(
            id=cls.GENERIC_ID,
            revision=1,
            target_width_px=800,
            max_slice_height_px=1280,
            min_slice_height_px=64,
            max_file_bytes=None,
            allowed_formats=("png", "jpeg", "webp"),
            updated="2026-08-23",
            note="Generic defaults; check the platform's current requirements before publishing.",
        )

    def allows_format(self, format_id: str) -> bool:
        return str(format_id).lower() in self.allowed_formats
