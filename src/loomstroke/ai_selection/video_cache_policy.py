"""Pure cleanup policy helpers for SAM3 Tier 6 video frame caches."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

SAM3_VIDEO_CACHE_ENTRY_SCHEMA_VERSION = "sam3.video_cache_entry.v1"
SAM3_VIDEO_CACHE_CLEANUP_PLAN_SCHEMA_VERSION = "sam3.video_cache_cleanup_plan.v1"

_VIDEO_CACHE_PREFIX = ("sam3", "video_cache")


class Sam3VideoCachePolicyError(ValueError):
    """Raised when a SAM3 video cache cleanup plan cannot be built safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3VideoCacheEntry:
    cache_id: str
    job_id: str
    cache_directory: str
    byte_size: int
    frame_count: int = 0
    created_ms: int = 0
    last_access_ms: int = 0
    source_manifest_id: str | None = None
    pinned: bool = False
    schema_version: str = SAM3_VIDEO_CACHE_ENTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "cache_id", _required_id(self.cache_id, "cache_id"))
        object.__setattr__(self, "job_id", _required_id(self.job_id, "job_id"))
        object.__setattr__(
            self,
            "cache_directory",
            _safe_cache_directory(self.cache_directory),
        )
        object.__setattr__(self, "byte_size", _non_negative_int(self.byte_size))
        object.__setattr__(self, "frame_count", _non_negative_int(self.frame_count))
        object.__setattr__(self, "created_ms", _non_negative_int(self.created_ms))
        object.__setattr__(self, "last_access_ms", _non_negative_int(self.last_access_ms))
        object.__setattr__(self, "source_manifest_id", _optional_id(self.source_manifest_id))
        object.__setattr__(self, "pinned", bool(self.pinned))

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "cacheId": self.cache_id,
            "jobId": self.job_id,
            "cacheDirectory": self.cache_directory,
            "byteSize": self.byte_size,
            "frameCount": self.frame_count,
            "createdMs": self.created_ms,
            "lastAccessMs": self.last_access_ms,
            "sourceManifestId": self.source_manifest_id,
            "pinned": self.pinned,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoCacheEntry:
        return cls(
            cache_id=str(value.get("cacheId", "")),
            job_id=str(value.get("jobId", "")),
            cache_directory=str(value.get("cacheDirectory", "")),
            byte_size=_non_negative_int(value.get("byteSize", 0)),
            frame_count=_non_negative_int(value.get("frameCount", 0)),
            created_ms=_non_negative_int(value.get("createdMs", 0)),
            last_access_ms=_non_negative_int(value.get("lastAccessMs", 0)),
            source_manifest_id=_optional_id(value.get("sourceManifestId")),
            pinned=bool(value.get("pinned", False)),
            schema_version=str(value.get("schemaVersion", SAM3_VIDEO_CACHE_ENTRY_SCHEMA_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class Sam3VideoCacheCleanupPlan:
    total_bytes: int
    projected_bytes: int
    max_total_bytes: int
    now_ms: int
    max_entry_age_ms: int | None = None
    delete_entry_ids: tuple[str, ...] = field(default_factory=tuple)
    delete_cache_directories: tuple[str, ...] = field(default_factory=tuple)
    keep_entry_ids: tuple[str, ...] = field(default_factory=tuple)
    reclaimable_bytes: int = 0
    delete_reasons: Mapping[str, str] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_CACHE_CLEANUP_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "total_bytes", _non_negative_int(self.total_bytes))
        object.__setattr__(self, "projected_bytes", _non_negative_int(self.projected_bytes))
        object.__setattr__(self, "max_total_bytes", _non_negative_int(self.max_total_bytes))
        object.__setattr__(self, "now_ms", _non_negative_int(self.now_ms))
        max_age = self.max_entry_age_ms
        object.__setattr__(
            self,
            "max_entry_age_ms",
            None if max_age is None else _non_negative_int(max_age),
        )
        object.__setattr__(self, "delete_entry_ids", tuple(self.delete_entry_ids))
        object.__setattr__(
            self,
            "delete_cache_directories",
            tuple(self.delete_cache_directories),
        )
        object.__setattr__(self, "keep_entry_ids", tuple(self.keep_entry_ids))
        object.__setattr__(self, "reclaimable_bytes", _non_negative_int(self.reclaimable_bytes))
        object.__setattr__(
            self,
            "delete_reasons",
            {str(key): str(value) for key, value in self.delete_reasons.items()},
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "totalBytes": self.total_bytes,
            "projectedBytes": self.projected_bytes,
            "maxTotalBytes": self.max_total_bytes,
            "nowMs": self.now_ms,
            "maxEntryAgeMs": self.max_entry_age_ms,
            "deleteEntryIds": list(self.delete_entry_ids),
            "deleteCacheDirectories": list(self.delete_cache_directories),
            "keepEntryIds": list(self.keep_entry_ids),
            "reclaimableBytes": self.reclaimable_bytes,
            "deleteReasons": dict(sorted(self.delete_reasons.items())),
        }


def build_video_cache_cleanup_plan(
    entries: Sequence[Sam3VideoCacheEntry],
    *,
    max_total_bytes: int,
    now_ms: int,
    max_entry_age_ms: int | None = None,
    protect_pinned: bool = True,
) -> Sam3VideoCacheCleanupPlan:
    """Build a deterministic cleanup plan for SAM3 video frame cache entries."""

    normalized_entries = _normalized_entries(entries)
    _ensure_unique_cache_ids(normalized_entries)

    normalized_max_total = _non_negative_int(max_total_bytes)
    normalized_now_ms = _non_negative_int(now_ms)
    normalized_max_age = None if max_entry_age_ms is None else _non_negative_int(max_entry_age_ms)
    total_bytes = sum(entry.byte_size for entry in normalized_entries)

    delete_reasons: dict[str, str] = {}
    for entry in normalized_entries:
        if _is_protected(entry, protect_pinned):
            continue
        if normalized_max_age is not None and (
            normalized_now_ms - entry.last_access_ms > normalized_max_age
        ):
            delete_reasons[entry.cache_id] = "expired"

    projected_bytes = _projected_bytes(normalized_entries, delete_reasons)
    if projected_bytes > normalized_max_total:
        for entry in sorted(
            normalized_entries,
            key=lambda item: (item.last_access_ms, item.created_ms, item.cache_id),
        ):
            if entry.cache_id in delete_reasons or _is_protected(entry, protect_pinned):
                continue
            delete_reasons[entry.cache_id] = "size_limit"
            projected_bytes -= entry.byte_size
            if projected_bytes <= normalized_max_total:
                break

    delete_entry_ids = tuple(
        entry.cache_id for entry in normalized_entries if entry.cache_id in delete_reasons
    )
    delete_cache_directories = tuple(
        entry.cache_directory for entry in normalized_entries if entry.cache_id in delete_reasons
    )
    keep_entry_ids = tuple(
        entry.cache_id for entry in normalized_entries if entry.cache_id not in delete_reasons
    )
    reclaimable_bytes = sum(
        entry.byte_size for entry in normalized_entries if entry.cache_id in delete_reasons
    )

    return Sam3VideoCacheCleanupPlan(
        total_bytes=total_bytes,
        projected_bytes=max(0, total_bytes - reclaimable_bytes),
        max_total_bytes=normalized_max_total,
        now_ms=normalized_now_ms,
        max_entry_age_ms=normalized_max_age,
        delete_entry_ids=delete_entry_ids,
        delete_cache_directories=delete_cache_directories,
        keep_entry_ids=keep_entry_ids,
        reclaimable_bytes=reclaimable_bytes,
        delete_reasons=delete_reasons,
    )


def _normalized_entries(entries: Sequence[Sam3VideoCacheEntry]) -> tuple[Sam3VideoCacheEntry, ...]:
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
        raise Sam3VideoCachePolicyError(
            "video_cache_entry_invalid",
            "SAM3 video cache cleanup plan requires cache entries.",
        )
    normalized: list[Sam3VideoCacheEntry] = []
    for entry in entries:
        if not isinstance(entry, Sam3VideoCacheEntry):
            raise Sam3VideoCachePolicyError(
                "video_cache_entry_invalid",
                "SAM3 video cache cleanup plan requires cache entries.",
            )
        normalized.append(entry)
    return tuple(normalized)


def _ensure_unique_cache_ids(entries: tuple[Sam3VideoCacheEntry, ...]) -> None:
    seen: set[str] = set()
    for entry in entries:
        if entry.cache_id in seen:
            raise Sam3VideoCachePolicyError(
                "video_cache_cleanup_entry_duplicate",
                "SAM3 video cache cleanup entries must have unique cache ids.",
            )
        seen.add(entry.cache_id)


def _is_protected(entry: Sam3VideoCacheEntry, protect_pinned: bool) -> bool:
    return bool(protect_pinned and entry.pinned)


def _projected_bytes(
    entries: tuple[Sam3VideoCacheEntry, ...],
    delete_reasons: Mapping[str, str],
) -> int:
    return sum(entry.byte_size for entry in entries if entry.cache_id not in delete_reasons)


def _required_id(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise Sam3VideoCachePolicyError(
            "video_cache_entry_id_missing",
            f"SAM3 video cache entry requires {field_name}.",
        )
    return text


def _optional_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_cache_directory(value: object) -> str:
    raw = str(value or "").strip()
    lowered = raw.lower()
    if not raw or "\\" in raw or "\0" in raw:
        raise _unsafe_directory_error()
    if "://" in raw or lowered.startswith(("http:", "https:", "file:")):
        raise _unsafe_directory_error()
    if raw.startswith("/") or raw.startswith("//") or ":" in raw:
        raise _unsafe_directory_error()
    parts = tuple(raw.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise _unsafe_directory_error()
    if len(parts) < 3 or parts[:2] != _VIDEO_CACHE_PREFIX:
        raise _unsafe_directory_error()
    return "/".join(parts)


def _unsafe_directory_error() -> Sam3VideoCachePolicyError:
    return Sam3VideoCachePolicyError(
        "video_cache_entry_directory_unsafe",
        "SAM3 video cache entry directory must be under sam3/video_cache.",
    )


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


__all__ = [
    "SAM3_VIDEO_CACHE_CLEANUP_PLAN_SCHEMA_VERSION",
    "SAM3_VIDEO_CACHE_ENTRY_SCHEMA_VERSION",
    "Sam3VideoCacheCleanupPlan",
    "Sam3VideoCacheEntry",
    "Sam3VideoCachePolicyError",
    "build_video_cache_cleanup_plan",
]
