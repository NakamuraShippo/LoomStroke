"""Pure request planning helpers for SAM3 Tier 6 video tracking."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from .metadata_scrub import scrub_sam3_metadata
from .video_source_manifest import Sam3VideoSourceManifest
from .video_tracker_model import Sam3VideoTrackObject

SAM3_VIDEO_TRACK_FRAME_RANGE_SCHEMA_VERSION = "sam3.video_track_frame_range.v1"
SAM3_VIDEO_TRACK_OPTIONS_SCHEMA_VERSION = "sam3.video_track_options.v1"
SAM3_VIDEO_TRACK_REQUEST_SCHEMA_VERSION = "sam3.video_track_request.v1"

Sam3VideoTrackMode = Literal["object_multiplex", "per_object"]
SAM3_VIDEO_TRACK_MODE_VALUES: tuple[Sam3VideoTrackMode, ...] = (
    "object_multiplex",
    "per_object",
)

_REDACTED = "<redacted>"


class Sam3VideoTrackRequestError(ValueError):
    """Raised when a SAM3 video track request cannot be represented safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackOptions:
    mode: Sam3VideoTrackMode = "object_multiplex"
    chunk_size: int = 48
    preview_downscale: float = 0.5
    max_objects: int = 16
    include_preview_assets: bool = True
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_TRACK_OPTIONS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _normalize_mode(self.mode))
        object.__setattr__(self, "chunk_size", max(1, _non_negative_int(self.chunk_size)))
        object.__setattr__(
            self,
            "preview_downscale",
            _clamped_float(self.preview_downscale, minimum=0.01, maximum=1.0, fallback=0.5),
        )
        object.__setattr__(self, "max_objects", max(1, _non_negative_int(self.max_objects)))
        object.__setattr__(self, "include_preview_assets", bool(self.include_preview_assets))
        object.__setattr__(self, "diagnostics", _scrubbed_mapping(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "mode": self.mode,
            "chunkSize": self.chunk_size,
            "previewDownscale": self.preview_downscale,
            "maxObjects": self.max_objects,
            "includePreviewAssets": self.include_preview_assets,
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoTrackOptions:
        return cls(
            mode=_normalize_mode(value.get("mode", "object_multiplex")),
            chunk_size=_non_negative_int(value.get("chunkSize", 48)),
            preview_downscale=_clamped_float(
                value.get("previewDownscale", 0.5),
                minimum=0.01,
                maximum=1.0,
                fallback=0.5,
            ),
            max_objects=_non_negative_int(value.get("maxObjects", 16)),
            include_preview_assets=bool(value.get("includePreviewAssets", True)),
            diagnostics=_dict(value.get("diagnostics")),
            schema_version=str(value.get("schemaVersion", SAM3_VIDEO_TRACK_OPTIONS_SCHEMA_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackFrameRange:
    range_index: int
    start_frame_index: int
    end_frame_index: int
    frame_count: int
    schema_version: str = SAM3_VIDEO_TRACK_FRAME_RANGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        start_frame_index = _non_negative_int(self.start_frame_index)
        end_frame_index = max(start_frame_index, _non_negative_int(self.end_frame_index))
        object.__setattr__(self, "range_index", _non_negative_int(self.range_index))
        object.__setattr__(self, "start_frame_index", start_frame_index)
        object.__setattr__(self, "end_frame_index", end_frame_index)
        object.__setattr__(self, "frame_count", max(1, _non_negative_int(self.frame_count)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "rangeIndex": self.range_index,
            "startFrameIndex": self.start_frame_index,
            "endFrameIndex": self.end_frame_index,
            "frameCount": self.frame_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoTrackFrameRange:
        return cls(
            range_index=_non_negative_int(value.get("rangeIndex", 0)),
            start_frame_index=_non_negative_int(value.get("startFrameIndex", 0)),
            end_frame_index=_non_negative_int(value.get("endFrameIndex", 0)),
            frame_count=max(1, _non_negative_int(value.get("frameCount", 1))),
            schema_version=str(
                value.get("schemaVersion", SAM3_VIDEO_TRACK_FRAME_RANGE_SCHEMA_VERSION)
            ),
        )


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackRequest:
    request_id: str
    source_manifest: Sam3VideoSourceManifest
    objects: tuple[Sam3VideoTrackObject, ...]
    options: Sam3VideoTrackOptions
    frame_ranges: tuple[Sam3VideoTrackFrameRange, ...] = field(default_factory=tuple)
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_TRACK_REQUEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        request_id = _required_id(self.request_id, "request_id")
        source_manifest = _source_manifest(self.source_manifest)
        options = _track_options(self.options)
        objects = tuple(self.objects)
        _validate_source_frames(source_manifest)
        _validate_track_objects(objects, source_manifest, options)
        frame_ranges = tuple(self.frame_ranges) or build_video_track_frame_ranges(
            source_manifest,
            chunk_size=options.chunk_size,
        )
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "source_manifest", source_manifest)
        object.__setattr__(self, "objects", objects)
        object.__setattr__(self, "options", options)
        object.__setattr__(self, "frame_ranges", tuple(frame_ranges))
        object.__setattr__(self, "diagnostics", _scrubbed_mapping(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "requestId": self.request_id,
            "sourceManifestId": self.source_manifest.manifest_id,
            "sourceKind": self.source_manifest.source_kind,
            "sourceAssetId": self.source_manifest.source_asset_id,
            "sourceManifest": self.source_manifest.to_dict(),
            "objectCount": len(self.objects),
            "objects": [item.to_dict() for item in self.objects],
            "frameCount": len(self.source_manifest.frames),
            "frameRanges": [item.to_dict() for item in self.frame_ranges],
            "options": self.options.to_dict(),
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoTrackRequest:
        return cls(
            request_id=str(value.get("requestId", "")),
            source_manifest=Sam3VideoSourceManifest.from_dict(_dict(value.get("sourceManifest"))),
            objects=tuple(
                Sam3VideoTrackObject.from_dict(item) for item in _dict_items(value.get("objects"))
            ),
            options=Sam3VideoTrackOptions.from_dict(_dict(value.get("options"))),
            frame_ranges=tuple(
                Sam3VideoTrackFrameRange.from_dict(item)
                for item in _dict_items(value.get("frameRanges"))
            ),
            diagnostics=_dict(value.get("diagnostics")),
            schema_version=str(value.get("schemaVersion", SAM3_VIDEO_TRACK_REQUEST_SCHEMA_VERSION)),
        )


def build_video_track_frame_ranges(
    manifest: Sam3VideoSourceManifest,
    *,
    chunk_size: int,
) -> tuple[Sam3VideoTrackFrameRange, ...]:
    """Build deterministic frame ranges from the manifest order."""

    source_manifest = _source_manifest(manifest)
    _validate_source_frames(source_manifest)
    normalized_chunk_size = max(1, _non_negative_int(chunk_size))
    ranges: list[Sam3VideoTrackFrameRange] = []
    frames = source_manifest.frames
    for range_index, start_offset in enumerate(range(0, len(frames), normalized_chunk_size)):
        chunk = frames[start_offset : start_offset + normalized_chunk_size]
        ranges.append(
            Sam3VideoTrackFrameRange(
                range_index=range_index,
                start_frame_index=chunk[0].frame_index,
                end_frame_index=chunk[-1].frame_index,
                frame_count=len(chunk),
            )
        )
    return tuple(ranges)


def build_video_track_request(
    *,
    request_id: str,
    source_manifest: Sam3VideoSourceManifest,
    objects: Sequence[Sam3VideoTrackObject],
    options: Sam3VideoTrackOptions | None = None,
    diagnostics: Mapping[str, object] | None = None,
) -> Sam3VideoTrackRequest:
    """Build a file-write-free SAM3 video tracking request."""

    track_options = options or Sam3VideoTrackOptions()
    return Sam3VideoTrackRequest(
        request_id=request_id,
        source_manifest=source_manifest,
        objects=tuple(objects),
        options=track_options,
        frame_ranges=build_video_track_frame_ranges(
            source_manifest,
            chunk_size=track_options.chunk_size,
        ),
        diagnostics=diagnostics or {},
    )


def _validate_source_frames(source_manifest: Sam3VideoSourceManifest) -> None:
    if not source_manifest.frames:
        raise Sam3VideoTrackRequestError(
            "video_track_request_source_frames_empty",
            "SAM3 video track request requires at least one source frame.",
        )


def _validate_track_objects(
    objects: tuple[Sam3VideoTrackObject, ...],
    source_manifest: Sam3VideoSourceManifest,
    options: Sam3VideoTrackOptions,
) -> None:
    if not objects:
        raise Sam3VideoTrackRequestError(
            "video_track_request_object_required",
            "SAM3 video track request requires at least one tracked object.",
        )
    if len(objects) > options.max_objects:
        raise Sam3VideoTrackRequestError(
            "video_track_request_too_many_objects",
            "SAM3 video track request exceeds the configured object limit.",
        )

    counts = Counter(item.object_id for item in objects)
    if any(count > 1 for count in counts.values()):
        raise Sam3VideoTrackRequestError(
            "video_track_request_duplicate_object",
            "SAM3 video track request object ids must be unique.",
        )

    frame_indices = {frame.frame_index for frame in source_manifest.frames}
    if any(item.first_frame_index not in frame_indices for item in objects):
        raise Sam3VideoTrackRequestError(
            "video_track_request_first_frame_missing",
            "SAM3 video track request object first frame must exist in the source manifest.",
        )


def _source_manifest(value: object) -> Sam3VideoSourceManifest:
    if not isinstance(value, Sam3VideoSourceManifest):
        raise Sam3VideoTrackRequestError(
            "video_track_request_source_invalid",
            "SAM3 video track request requires a source manifest.",
        )
    return value


def _track_options(value: object) -> Sam3VideoTrackOptions:
    if not isinstance(value, Sam3VideoTrackOptions):
        raise Sam3VideoTrackRequestError(
            "video_track_request_options_invalid",
            "SAM3 video track request requires video track options.",
        )
    return value


def _normalize_mode(value: object) -> Sam3VideoTrackMode:
    text = str(value or "").strip()
    if text not in SAM3_VIDEO_TRACK_MODE_VALUES:
        raise Sam3VideoTrackRequestError(
            "video_track_options_mode_invalid",
            "SAM3 video track mode is not supported.",
        )
    return cast(Sam3VideoTrackMode, text)


def _required_id(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise Sam3VideoTrackRequestError(
            "video_track_request_id_missing",
            f"SAM3 video track request requires {field_name}.",
        )
    return text


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _clamped_float(value: object, *, minimum: float, maximum: float, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    if not math.isfinite(number):
        number = fallback
    return min(maximum, max(minimum, number))


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
    "SAM3_VIDEO_TRACK_FRAME_RANGE_SCHEMA_VERSION",
    "SAM3_VIDEO_TRACK_MODE_VALUES",
    "SAM3_VIDEO_TRACK_OPTIONS_SCHEMA_VERSION",
    "SAM3_VIDEO_TRACK_REQUEST_SCHEMA_VERSION",
    "Sam3VideoTrackFrameRange",
    "Sam3VideoTrackMode",
    "Sam3VideoTrackOptions",
    "Sam3VideoTrackRequest",
    "Sam3VideoTrackRequestError",
    "build_video_track_frame_ranges",
    "build_video_track_request",
]
