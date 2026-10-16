"""Preflight profiles (CT-16 spec 16 §5 / §8.1 / §10).

Built-in profiles are code assets (digital / webtoon / print); only the
stable ``profile_id`` + ``version`` may ever be persisted.  Printer-house
specifics are deliberately not hard-coded (§4) — they arrive as user
profiles later.  ``severity_overrides`` and ``exceptions`` let a profile
raise/lower a rule or turn it off; ``params`` feed rule thresholds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Literal

__all__ = ["MangaPreflightProfile"]

TargetKind = Literal["digital", "webtoon", "print"]

_ALL_RULES: tuple[str, ...] = (
    "document.page_count",
    "document.page_size",
    "raster.dpi",
    "raster.empty_page",
    "text.overflow",
    "text.empty",
    "text.min_font_size",
    "text.missing_font",
    "color.icc",
    "print.bleed",
    "resource.linked",
)


def _frozen(mapping: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(mapping or {}))


@dataclass(frozen=True, slots=True)
class MangaPreflightProfile:
    profile_id: str
    version: int
    target_kind: TargetKind
    rules: tuple[str, ...] = _ALL_RULES
    severity_overrides: Mapping[str, str] = field(default_factory=lambda: _frozen({}))
    exceptions: tuple[str, ...] = ()
    params: Mapping[str, Any] = field(default_factory=lambda: _frozen({}))

    BUILTIN_IDS: ClassVar[tuple[str, ...]] = (
        "loomstroke.digital",
        "loomstroke.webtoon",
        "loomstroke.print_mono",
    )

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("profile_id is required")
        if self.version < 1:
            raise ValueError("profile version must be >= 1")
        object.__setattr__(self, "severity_overrides", _frozen(self.severity_overrides))
        object.__setattr__(self, "params", _frozen(self.params))

    # -- built-ins ---------------------------------------------------------------

    @classmethod
    def digital(cls) -> MangaPreflightProfile:
        return cls(
            profile_id="loomstroke.digital",
            version=1,
            target_kind="digital",
            params={"min_dpi": 72.0, "min_font_pt": 6.0, "min_page_px": 64},
        )

    @classmethod
    def webtoon(cls) -> MangaPreflightProfile:
        return cls(
            profile_id="loomstroke.webtoon",
            version=1,
            target_kind="webtoon",
            exceptions=("document.page_size",),  # mixed heights are normal
            params={"min_dpi": 72.0, "min_font_pt": 6.0, "min_page_px": 64},
        )

    @classmethod
    def print_mono(cls) -> MangaPreflightProfile:
        return cls(
            profile_id="loomstroke.print_mono",
            version=1,
            target_kind="print",
            severity_overrides={"text.overflow": "fatal", "raster.dpi": "fatal"},
            params={"min_dpi": 600.0, "min_font_pt": 5.0, "min_page_px": 256},
        )

    @classmethod
    def builtin(cls, profile_id: str) -> MangaPreflightProfile:
        for factory in (cls.digital, cls.webtoon, cls.print_mono):
            profile = factory()
            if profile.profile_id == profile_id:
                return profile
        raise KeyError(profile_id)

    @classmethod
    def builtins(cls) -> tuple[MangaPreflightProfile, ...]:
        return (cls.digital(), cls.webtoon(), cls.print_mono())

    # -- queries -----------------------------------------------------------------

    def enabled_rules(self) -> tuple[str, ...]:
        return tuple(rule for rule in self.rules if rule not in self.exceptions)

    def severity_for(self, rule_id: str, default: str) -> str:
        return str(self.severity_overrides.get(rule_id, default))

    def param(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)
