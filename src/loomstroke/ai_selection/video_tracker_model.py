"""Pure data model for SAM3 Tier 6 video tracking artifacts."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .data_model import Sam3Prompt
from .metadata_scrub import scrub_sam3_metadata

SAM3_VIDEO_TRACK_OBJECT_SCHEMA_VERSION = "sam3.video_track_object.v1"
SAM3_VIDEO_MASK_FRAME_SCHEMA_VERSION = "sam3.video_mask_frame.v1"
SAM3_VIDEO_TRACK_RESULT_SCHEMA_VERSION = "sam3.video_track_result.v1"

_WARNING_CODE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789" "_.:-"
)


class Sam3VideoTrackerModelError(ValueError):
    """Raised when a SAM3 video tracking model cannot be represented safely."""


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackObject:
    object_id: str
    label: str
    prompt: Sam3Prompt
    first_frame_index: int
    candidate_id: str | None = None
    color_hint: str | None = None
    schema_version: str = SAM3_VIDEO_TRACK_OBJECT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "object_id", _required_id(self.object_id, "object_id"))
        object.__setattr__(self, "label", str(self.label or "").strip())
        object.__setattr__(self, "first_frame_index", _non_negative_int(self.first_frame_index))
        object.__setattr__(self, "candidate_id", _optional_id(self.candidate_id))
        object.__setattr__(self, "color_hint", _optional_str(self.color_hint))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "objectId": self.object_id,
            "label": self.label,
            "prompt": self.prompt.to_dict(),
            "firstFrameIndex": self.first_frame_index,
            "candidateId": self.candidate_id,
            "colorHint": self.color_hint,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoTrackObject:
        return cls(
            object_id=str(value.get("objectId", "")),
            label=str(value.get("label", "")),
            prompt=Sam3Prompt.from_dict(_dict(value.get("prompt"))),
            first_frame_index=_non_negative_int(value.get("firstFrameIndex", 0)),
            candidate_id=_optional_id(value.get("candidateId")),
            color_hint=_optional_str(value.get("colorHint")),
            schema_version=str(value.get("schemaVersion", SAM3_VIDEO_TRACK_OBJECT_SCHEMA_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class Sam3VideoMaskFrame:
    frame_index: int
    timestamp_ms: int
    object_masks: Mapping[str, str]
    preview_asset_id: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SAM3_VIDEO_MASK_FRAME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "frame_index", _non_negative_int(self.frame_index))
        object.__setattr__(self, "timestamp_ms", _non_negative_int(self.timestamp_ms))
        object.__setattr__(self, "object_masks", _object_mask_refs(self.object_masks))
        object.__setattr__(self, "preview_asset_id", _optional_id(self.preview_asset_id))
        object.__setattr__(self, "warnings", _warning_codes(self.warnings))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "frameIndex": self.frame_index,
            "timestampMs": self.timestamp_ms,
            "objectMasks": dict(self.object_masks),
            "previewAssetId": self.preview_asset_id,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoMaskFrame:
        return cls(
            frame_index=_non_negative_int(value.get("frameIndex", 0)),
            timestamp_ms=_non_negative_int(value.get("timestampMs", 0)),
            object_masks=_object_masks_from_payload(value.get("objectMasks")),
            preview_asset_id=_optional_id(value.get("previewAssetId")),
            warnings=tuple(str(item) for item in _list_items(value.get("warnings"))),
            schema_version=str(value.get("schemaVersion", SAM3_VIDEO_MASK_FRAME_SCHEMA_VERSION)),
        )


@dataclass(frozen=True, slots=True)
class Sam3VideoTrackResult:
    track_id: str
    source_video_asset_id: str
    objects: tuple[Sam3VideoTrackObject, ...]
    frames: tuple[Sam3VideoMaskFrame, ...]
    fps: float
    width: int
    height: int
    package_path: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SAM3_VIDEO_TRACK_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "track_id", _required_id(self.track_id, "track_id"))
        object.__setattr__(
            self,
            "source_video_asset_id",
            _required_id(self.source_video_asset_id, "source_video_asset_id"),
        )
        object.__setattr__(self, "objects", tuple(self.objects))
        object.__setattr__(self, "frames", tuple(self.frames))
        object.__setattr__(self, "fps", _non_negative_finite_float(self.fps))
        object.__setattr__(self, "width", _non_negative_int(self.width))
        object.__setattr__(self, "height", _non_negative_int(self.height))
        object.__setattr__(self, "package_path", _optional_str(self.package_path))
        object.__setattr__(self, "diagnostics", _dict(self.diagnostics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "trackId": self.track_id,
            "sourceVideoAssetId": self.source_video_asset_id,
            "objects": [item.to_dict() for item in self.objects],
            "frames": [frame.to_dict() for frame in self.frames],
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "packagePath": self.package_path,
            "diagnostics": scrub_sam3_metadata(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Sam3VideoTrackResult:
        return cls(
            track_id=str(value.get("trackId", "")),
            source_video_asset_id=str(value.get("sourceVideoAssetId", "")),
            objects=tuple(
                Sam3VideoTrackObject.from_dict(item) for item in _dict_items(value.get("objects"))
            ),
            frames=tuple(
                Sam3VideoMaskFrame.from_dict(item) for item in _dict_items(value.get("frames"))
            ),
            fps=_non_negative_finite_float(value.get("fps", 0.0)),
            width=_non_negative_int(value.get("width", 0)),
            height=_non_negative_int(value.get("height", 0)),
            package_path=_optional_str(value.get("packagePath")),
            diagnostics=_dict(value.get("diagnostics")),
            schema_version=str(value.get("schemaVersion", SAM3_VIDEO_TRACK_RESULT_SCHEMA_VERSION)),
        )


def _required_id(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise Sam3VideoTrackerModelError(f"SAM3 video tracking requires {field_name}.")
    return text


def _optional_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _non_negative_finite_float(value: object) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, number)


def _object_mask_refs(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    refs: dict[str, str] = {}
    for raw_object_id, raw_mask_asset_id in value.items():
        object_id = _optional_id(raw_object_id)
        mask_asset_id = _optional_id(raw_mask_asset_id)
        if object_id is None or mask_asset_id is None:
            continue
        refs[object_id] = mask_asset_id
    return dict(sorted(refs.items()))


def _object_masks_from_payload(value: object) -> dict[str, str]:
    return _object_mask_refs(value) if isinstance(value, Mapping) else {}


def _dict(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(scrub_sam3_metadata(value))


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


__all__ = [
    "SAM3_VIDEO_MASK_FRAME_SCHEMA_VERSION",
    "SAM3_VIDEO_TRACK_OBJECT_SCHEMA_VERSION",
    "SAM3_VIDEO_TRACK_RESULT_SCHEMA_VERSION",
    "Sam3VideoMaskFrame",
    "Sam3VideoTrackObject",
    "Sam3VideoTrackResult",
    "Sam3VideoTrackerModelError",
]
