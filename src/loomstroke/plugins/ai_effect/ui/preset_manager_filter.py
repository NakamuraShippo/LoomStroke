"""Filter and sort pipeline for the AI Effect preset manager."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PresetFilterState:
    category: str | None = None
    gateway: str | None = None
    source: str | None = None
    search_query: str = ""
    sort_mode: str = "recent"


@dataclass(frozen=True, slots=True)
class PresetSidebarCounts:
    total: int
    categories: dict[str, int]
    gateways: dict[str, int]
    sources: dict[str, int]


class PresetFilterEngine:
    """Pure filter/search/sort helper for PresetManagerWindow."""

    def apply(self, presets: list[Any], state: PresetFilterState) -> list[Any]:
        filtered = [preset for preset in presets if self.matches(preset, state)]
        return self.sort(filtered, state.sort_mode)

    def matches(self, preset: Any, state: PresetFilterState) -> bool:
        if state.category is not None:
            preset_cat = str(getattr(preset, "category", "") or "").strip().lower()
            if preset_cat != state.category:
                return False
        if state.gateway is not None:
            preset_gw = str(getattr(preset, "gateway_id", "") or "").strip().lower()
            if preset_gw != state.gateway:
                return False
        if state.source is not None:
            preset_src = str(getattr(preset, "source", "") or "").strip().lower()
            if state.source == "user":
                if preset_src not in ("user", "imported"):
                    return False
            elif preset_src != state.source:
                return False
        if state.search_query:
            haystack = self.search_haystack(preset)
            if state.search_query not in haystack:
                return False
        return True

    @staticmethod
    def search_haystack(preset: Any) -> str:
        parts: list[str] = []
        name = str(getattr(preset, "name", "") or "")
        if name:
            parts.append(name)
        manifest = getattr(preset, "manifest", None)
        if isinstance(manifest, dict):
            metadata = manifest.get("metadata", {})
            if isinstance(metadata, dict):
                tags = metadata.get("tags", [])
                if isinstance(tags, list):
                    parts.extend(str(tag) for tag in tags if tag)
                description = metadata.get("description", "")
                if isinstance(description, str) and description:
                    parts.append(description)
        return " ".join(parts).lower()

    @staticmethod
    def sort(presets: list[Any], sort_mode: str) -> list[Any]:
        if sort_mode == "name":
            return sorted(presets, key=lambda p: str(getattr(p, "name", "") or "").lower())
        if sort_mode == "category":
            return sorted(
                presets,
                key=lambda p: (
                    str(getattr(p, "category", "") or "").lower(),
                    str(getattr(p, "name", "") or "").lower(),
                ),
            )
        if sort_mode == "created":

            def _mtime(preset: Any) -> float:
                path = getattr(preset, "lsfx_path", None)
                if path is None:
                    return 0.0
                try:
                    return float(path.stat().st_mtime)
                except OSError:
                    return 0.0

            return sorted(presets, key=_mtime, reverse=True)
        return list(presets)

    @staticmethod
    def sidebar_counts(presets: list[Any]) -> PresetSidebarCounts:
        cat_counts: Counter[str] = Counter()
        gw_counts: Counter[str] = Counter()
        src_counts: Counter[str] = Counter()
        for preset in presets:
            cat = str(getattr(preset, "category", "") or "").strip().lower()
            cat_counts[cat] += 1
            gw = str(getattr(preset, "gateway_id", "") or "").strip().lower()
            gw_counts[gw] += 1
            src = str(getattr(preset, "source", "") or "").strip().lower()
            if src in ("user", "imported"):
                src_counts["user"] += 1
            else:
                src_counts[src] += 1
        return PresetSidebarCounts(
            total=len(presets),
            categories=dict(cat_counts),
            gateways=dict(gw_counts),
            sources=dict(src_counts),
        )


__all__ = ["PresetFilterEngine", "PresetFilterState", "PresetSidebarCounts"]
