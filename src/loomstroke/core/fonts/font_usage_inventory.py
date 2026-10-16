"""Document-wide font inventory (CT-25 ``FontUsageInventory``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .font_resolution_result import FontResolutionResult
from .font_usage import FontUsage

__all__ = ["FontUsageInventory"]


@dataclass(frozen=True, slots=True)
class FontUsageInventory:
    usages: tuple[FontUsage, ...] = field(default_factory=tuple)
    resolutions: Mapping[str, FontResolutionResult] = field(
        default_factory=lambda: MappingProxyType({})
    )  # keyed by FontRequest.key

    def __post_init__(self) -> None:
        object.__setattr__(self, "resolutions", MappingProxyType(dict(self.resolutions)))

    def resolution_for(self, usage: FontUsage) -> FontResolutionResult | None:
        return self.resolutions.get(usage.request.key)

    def missing(self) -> tuple[FontUsage, ...]:
        return tuple(
            usage
            for usage in self.usages
            if (res := self.resolution_for(usage)) is not None and not res.is_available
        )

    def fallback(self) -> tuple[FontUsage, ...]:
        return tuple(
            usage
            for usage in self.usages
            if (res := self.resolution_for(usage)) is not None and res.uses_fallback
        )

    def usage_for_family(self, family: str, style: str | None = None) -> FontUsage | None:
        wanted_family = family.strip().casefold()
        wanted_style = (style or "").strip().casefold()
        for usage in self.usages:
            if usage.request.family.strip().casefold() != wanted_family:
                continue
            if style is None or (usage.request.style or "").strip().casefold() == wanted_style:
                return usage
        return None

    def counts(self) -> dict[str, int]:
        return {
            "fonts": len(self.usages),
            "missing": len(self.missing()),
            "objects": sum(usage.object_count for usage in self.usages),
        }
