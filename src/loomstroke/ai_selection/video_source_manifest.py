"""Pure source manifest helpers for SAM3 Tier 6 video tracking inputs."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from .metadata_scrub import scrub_sam3_metadata

SAM3_VIDEO_SOURCE_FRAME_REF_SCHEMA_VERSION = "sam3.video_source_frame_ref.v1"
SAM3_VIDEO_SOURCE_MANIFEST_SCHEMA_VERSION = "sam3.video_source_manifest.v1"
SAM3_VIDEO_DECODE_PLAN_SCHEMA_VERSION = "sam3.video_decode_plan.v1"

Sam3VideoSourceKind = Literal[
    "video_file",
    "image_sequence",
    "current_animation",
    "external_package",
]
SAM3_VIDEO_SOURCE_KIND_VALUES: tuple[Sam3VideoSourceKind, ...] = (
    "video_file",
    "image_sequence",
    "current_animation",
    "external_package",
)

_SAFE_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789" "._-"
)
_WARNING_CODE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789" "_.:-"
)
_REDACTED = "<redacted>"


class Sam3VideoSourceManifestError(ValueError):
    """Raised when a SAM3 video source manifest cannot be represented safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3VideoSourceFrameRef:
    frame_index: int
    timestamp_ms: int
    asset_id: str | None = None
    cache_path: str | None = None
    width: int = 0
    height: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SAM3_VIDEO_SOURCE_FRAME_REF_SCHEMA_VERSION

    def __post_init__(self) -> None:
        asset_id = _optional_id(self.asset_id)
        cache_path = _optional_safe_relative_path(
            self.cache_path,
            code="video_source_cache_path_unsafe",
            message="SAM3 video source frame cache path must be package-relative.",
        )
        if asset_id is None and cache_path is None:
            raise Sam3VideoSourceManifestError(
                "video_source_frame_ref_empty",
                "SAM3 video source frame requires an asset id or cache path.",
            )
        object.__setattr__(self, "frame_index", _non_negative_int(self.frame_index))
        object.__setattr__(self, "timestamp_ms", _non_negative_int(self.timestamp_ms))
        object.__setattr__(self, "asset_id", asset_id)
        object.__setattr__(self, "cache_path", cache_path)
        object.__setattr__(self, "width", _non_negative_int(self.width))
        object.__setattr__(self, "height", _non_negative_int(self.height))
        object.__setattr__(self, "warnings", _warning_codes(self.warnings))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "frameIndex": self.frame_index,
            "timestampMs": self.timestamp_ms,
            "assetId": self.asset_id,
            "cachePath": self.cache_path,
            "width": self.width,
            "height": self.height,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoSourceFrameRef:
        return cls(
            frame_index=_non_negative_int(value.get("frameIndex", 0)),
            timestamp_ms=_non_negative_int(value.get("timestampMs", 0)),
            asset_id=_optional_id(value.get("assetId")),
            cache_path=_optional_id(value.get("cachePath")),
            width=_non_negative_int(value.get("width", 0)),
            height=_non_negative_int(value.get("height", 0)),
            warnings=tuple(str(item) for item in _list_items(value.get("warnings"))),
            schema_version=str(
                value.get("schemaVersion", SAM3_VIDEO_SOURCE_FRAME_REF_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True, slots=True)
class Sam3VideoSourceManifest:
    manifest_id: str
    job_id: str
    source_kind: Sam3VideoSourceKind
    source_asset_id: str
    cache_directory: str
    frames: tuple[Sam3VideoSourceFrameRef, ...]
    fps: float
    width: int
    height: int
    duration_ms: int
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_SOURCE_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        frames = _sorted_unique_frames(tuple(self.frames))
        object.__setattr__(self, "manifest_id", _required_id(self.manifest_id, "manifest_id"))
        object.__setattr__(self, "job_id", _required_id(self.job_id, "job_id"))
        object.__setattr__(
            self,
            "source_kind",
            _normalize_source_kind(self.source_kind),
        )
        object.__setattr__(
            self,
            "source_asset_id",
            _required_id(self.source_asset_id, "source_asset_id"),
        )
        object.__setattr__(
            self,
            "cache_directory",
            _safe_relative_path(
                self.cache_directory,
                code="video_source_cache_directory_unsafe",
                message="SAM3 video source cache directory must be user-data-relative.",
            ),
        )
        object.__setattr__(self, "frames", frames)
        object.__setattr__(self, "fps", _non_negative_finite_float(self.fps))
        object.__setattr__(self, "width", _non_negative_int(self.width))
        object.__setattr__(self, "height", _non_negative_int(self.height))
        object.__setattr__(self, "duration_ms", _non_negative_int(self.duration_ms))
        object.__setattr__(self, "diagnostics", _scrubbed_mapping(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "manifestId": self.manifest_id,
            "jobId": self.job_id,
            "sourceKind": self.source_kind,
            "sourceAssetId": self.source_asset_id,
            "cacheDirectory": self.cache_directory,
            "frameCount": len(self.frames),
            "frames": [frame.to_dict() for frame in self.frames],
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "durationMs": self.duration_ms,
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoSourceManifest:
        return cls(
            manifest_id=str(value.get("manifestId", "")),
            job_id=str(value.get("jobId", "")),
            source_kind=_normalize_source_kind(value.get("sourceKind")),
            source_asset_id=str(value.get("sourceAssetId", "")),
            cache_directory=str(value.get("cacheDirectory", "")),
            frames=tuple(
                Sam3VideoSourceFrameRef.from_dict(item) for item in _dict_items(value.get("frames"))
            ),
            fps=_non_negative_finite_float(value.get("fps", 0.0)),
            width=_non_negative_int(value.get("width", 0)),
            height=_non_negative_int(value.get("height", 0)),
            duration_ms=_non_negative_int(value.get("durationMs", 0)),
            diagnostics=_dict(value.get("diagnostics")),
            schema_version=str(
                value.get("schemaVersion", SAM3_VIDEO_SOURCE_MANIFEST_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True, slots=True)
class Sam3VideoDecodePlan:
    job_id: str
    source_kind: Sam3VideoSourceKind
    source_asset_id: str
    cache_directory: str
    frame_pattern: str = "frames/frame_{frameNumber}.png"
    requested_fps: float = 0.0
    max_frame_count: int | None = None
    selected_frame_indices: tuple[int, ...] = field(default_factory=tuple)
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_DECODE_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "job_id", _required_id(self.job_id, "job_id"))
        object.__setattr__(
            self,
            "source_kind",
            _normalize_source_kind(self.source_kind),
        )
        object.__setattr__(
            self,
            "source_asset_id",
            _required_id(self.source_asset_id, "source_asset_id"),
        )
        object.__setattr__(
            self,
            "cache_directory",
            _safe_relative_path(
                self.cache_directory,
                code="video_source_cache_directory_unsafe",
                message="SAM3 video source cache directory must be user-data-relative.",
            ),
        )
        object.__setattr__(
            self,
            "frame_pattern",
            _safe_relative_path(
                self.frame_pattern,
                code="video_source_frame_pattern_unsafe",
                message="SAM3 video source frame pattern must be package-relative.",
            ),
        )
        object.__setattr__(self, "requested_fps", _non_negative_finite_float(self.requested_fps))
        object.__setattr__(
            self,
            "max_frame_count",
            _optional_non_negative_int(self.max_frame_count),
        )
        object.__setattr__(
            self,
            "selected_frame_indices",
            _selected_frame_indices(self.selected_frame_indices),
        )
        object.__setattr__(self, "diagnostics", _scrubbed_mapping(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "jobId": self.job_id,
            "sourceKind": self.source_kind,
            "sourceAssetId": self.source_asset_id,
            "cacheDirectory": self.cache_directory,
            "framePattern": self.frame_pattern,
            "requestedFps": self.requested_fps,
            "maxFrameCount": self.max_frame_count,
            "selectedFrameIndices": list(self.selected_frame_indices),
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoDecodePlan:
        return cls(
            job_id=str(value.get("jobId", "")),
            source_kind=_normalize_source_kind(value.get("sourceKind")),
            source_asset_id=str(value.get("sourceAssetId", "")),
            cache_directory=str(value.get("cacheDirectory", "")),
            frame_pattern=str(value.get("framePattern", "frames/frame_{frameNumber}.png")),
            requested_fps=_non_negative_finite_float(value.get("requestedFps", 0.0)),
            max_frame_count=_optional_non_negative_int(value.get("maxFrameCount")),
            selected_frame_indices=_selected_frame_indices(value.get("selectedFrameIndices")),
            diagnostics=_dict(value.get("diagnostics")),
            schema_version=str(value.get("schemaVersion", SAM3_VIDEO_DECODE_PLAN_SCHEMA_VERSION)),
        )


def build_video_decode_plan(
    *,
    job_id: str,
    source_kind: Sam3VideoSourceKind,
    source_asset_id: str,
    requested_fps: float = 0.0,
    cache_directory: str | None = None,
    frame_pattern: str = "frames/frame_{frameNumber}.png",
    max_frame_count: int | None = None,
    selected_frame_indices: Iterable[int] = (),
    diagnostics: Mapping[str, object] | None = None,
) -> Sam3VideoDecodePlan:
    """Build a file-write-free plan for decoding a source into cache frames."""

    return Sam3VideoDecodePlan(
        job_id=job_id,
        source_kind=source_kind,
        source_asset_id=source_asset_id,
        cache_directory=cache_directory or _default_cache_directory(job_id),
        frame_pattern=frame_pattern,
        requested_fps=requested_fps,
        max_frame_count=max_frame_count,
        selected_frame_indices=tuple(selected_frame_indices),
        diagnostics=diagnostics or {},
    )


def build_video_source_manifest_from_frame_assets(
    *,
    manifest_id: str,
    job_id: str,
    source_kind: Sam3VideoSourceKind,
    source_asset_id: str,
    frame_asset_ids: Sequence[str],
    fps: float,
    width: int,
    height: int,
    cache_directory: str | None = None,
    diagnostics: Mapping[str, object] | None = None,
) -> Sam3VideoSourceManifest:
    """Build a deterministic source manifest from already decoded frame assets."""

    normalized_fps = _non_negative_finite_float(fps)
    normalized_width = _non_negative_int(width)
    normalized_height = _non_negative_int(height)
    frames = tuple(
        Sam3VideoSourceFrameRef(
            frame_index=index,
            timestamp_ms=_timestamp_ms_for_index(index, normalized_fps),
            asset_id=_optional_id(asset_id),
            cache_path=_frame_cache_path(index),
            width=normalized_width,
            height=normalized_height,
        )
        for index, asset_id in enumerate(frame_asset_ids)
    )
    return Sam3VideoSourceManifest(
        manifest_id=manifest_id,
        job_id=job_id,
        source_kind=source_kind,
        source_asset_id=source_asset_id,
        cache_directory=cache_directory or _default_cache_directory(job_id),
        frames=frames,
        fps=normalized_fps,
        width=normalized_width,
        height=normalized_height,
        duration_ms=_duration_ms_for_count(len(frame_asset_ids), normalized_fps),
        diagnostics=diagnostics or {},
    )


def _sorted_unique_frames(
    frames: tuple[Sam3VideoSourceFrameRef, ...],
) -> tuple[Sam3VideoSourceFrameRef, ...]:
    counts = Counter(frame.frame_index for frame in frames)
    duplicate_indices = [frame_index for frame_index, count in sorted(counts.items()) if count > 1]
    if duplicate_indices:
        raise Sam3VideoSourceManifestError(
            "video_source_duplicate_frame_index",
            "SAM3 video source manifest frame indices must be unique.",
        )
    return tuple(sorted(frames, key=lambda frame: frame.frame_index))


def _normalize_source_kind(value: object) -> Sam3VideoSourceKind:
    text = str(value or "").strip()
    if text not in SAM3_VIDEO_SOURCE_KIND_VALUES:
        raise Sam3VideoSourceManifestError(
            "video_source_kind_invalid",
            "SAM3 video source kind is not supported.",
        )
    return cast(Sam3VideoSourceKind, text)


def _required_id(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise Sam3VideoSourceManifestError(
            "video_source_required_id_missing",
            f"SAM3 video source requires {field_name}.",
        )
    return text


def _optional_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _optional_non_negative_int(value: object) -> int | None:
    if value is None:
        return None
    return _non_negative_int(value)


def _non_negative_finite_float(value: object) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, number)


def _selected_frame_indices(value: object) -> tuple[int, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Iterable):
        return ()
    return tuple(sorted({_non_negative_int(item) for item in value}))


def _frame_cache_path(frame_index: int) -> str:
    return f"frames/frame_{max(0, int(frame_index)) + 1:06d}.png"


def _timestamp_ms_for_index(frame_index: int, fps: float) -> int:
    if fps <= 0:
        return 0
    return _non_negative_int(round(max(0, int(frame_index)) * 1000.0 / fps))


def _duration_ms_for_count(frame_count: int, fps: float) -> int:
    if fps <= 0:
        return 0
    return _non_negative_int(round(max(0, int(frame_count)) * 1000.0 / fps))


def _default_cache_directory(job_id: object) -> str:
    return f"sam3/video_cache/{_path_name(job_id, fallback='job')}"


def _path_name(value: object, *, fallback: str) -> str:
    text = str(value or "").strip()
    slug = "".join(char if char in _SAFE_NAME_CHARS else "_" for char in text).strip("._-")
    if not slug or slug in {".", ".."}:
        return fallback
    return slug[:96]


def _optional_safe_relative_path(value: object, *, code: str, message: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _safe_relative_path(text, code=code, message=message)


def _safe_relative_path(value: object, *, code: str, message: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    lowered = text.lower()
    if not text:
        raise Sam3VideoSourceManifestError(code, message)
    if "\0" in text or "://" in text or lowered.startswith(("http:", "https:", "file:")):
        raise Sam3VideoSourceManifestError(code, message)
    if text.startswith("/") or text.startswith("//") or ":" in text:
        raise Sam3VideoSourceManifestError(code, message)
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise Sam3VideoSourceManifestError(code, message)
    return "/".join(parts)


def _dict(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return _scrubbed_mapping(value)


def _dict_items(value: object) -> tuple[dict[str, Any], ...]:
    return tuple(item for item in _list_items(value) if isinstance(item, dict))


def _list_items(value: object) -> tuple[Any, ...]:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return ()


def _warning_codes(values: object) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        return ()
    codes: list[str] = []
    for value in values:
        code = _warning_code(value)
        if code:
            codes.append(code)
    return tuple(codes)


def _warning_code(value: object) -> str:
    text = "_".join(str(value or "").strip().split())
    return "".join(char for char in text if char in _WARNING_CODE_CHARS)[:80]


def _scrubbed_mapping(value: Mapping[str, object]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    scrubbed = scrub_sam3_metadata(value)
    redacted = _redact_source_locations(scrubbed)
    return dict(redacted) if isinstance(redacted, Mapping) else {}


def _redact_source_locations(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _redact_source_locations(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_source_locations(item) for item in value]
    if isinstance(value, str) and _looks_like_source_location(value):
        return _REDACTED
    return value


def _looks_like_source_location(value: str) -> bool:
    text = value.strip()
    lowered = text.lower()
    return (
        "://" in text
        or lowered.startswith(("http:", "https:", "file:"))
        or text.startswith(("/", "\\", "//", "\\\\"))
        or (len(text) >= 2 and text[1] == ":" and text[0].isalpha())
    )


__all__ = [
    "SAM3_VIDEO_DECODE_PLAN_SCHEMA_VERSION",
    "SAM3_VIDEO_SOURCE_FRAME_REF_SCHEMA_VERSION",
    "SAM3_VIDEO_SOURCE_KIND_VALUES",
    "SAM3_VIDEO_SOURCE_MANIFEST_SCHEMA_VERSION",
    "Sam3VideoDecodePlan",
    "Sam3VideoSourceFrameRef",
    "Sam3VideoSourceKind",
    "Sam3VideoSourceManifest",
    "Sam3VideoSourceManifestError",
    "build_video_decode_plan",
    "build_video_source_manifest_from_frame_assets",
]
