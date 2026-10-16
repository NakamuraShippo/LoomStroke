"""Export payload helpers for SAM3 video mask packages."""

from __future__ import annotations

from collections.abc import Mapping

from .metadata_scrub import scrub_sam3_metadata
from .video_qa import (
    SAM3_VIDEO_TRACK_QA_SCORECARD_SCHEMA_VERSION,
    analyze_video_track_result,
)
from .video_tracker_model import Sam3VideoMaskFrame, Sam3VideoTrackResult

SAM3_VIDEO_MASK_PACKAGE_SCHEMA_VERSION = "sam3.video_mask_package.v1"
SAM3_VIDEO_MASK_EXPORT_PAYLOAD_SCHEMA_VERSION = "sam3.video_mask_export_payload.v1"
SAM3_VIDEO_TRACK_LINEAGE_SCHEMA_VERSION = "sam3.video_track_lineage.v1"

_SAFE_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789" "._-"
)


class Sam3VideoMaskExportError(ValueError):
    """Raised when a video mask package export payload cannot be built."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_video_mask_package_export_payload(
    result: Sam3VideoTrackResult,
    *,
    package_id: str | None = None,
    metadata_path: str = "video_mask_package.json",
    include_previews: bool = True,
) -> dict[str, object]:
    """Build a file-write-free export payload for a SAM3 video mask package."""

    if not isinstance(result, Sam3VideoTrackResult):
        raise Sam3VideoMaskExportError(
            "video_mask_export_result_invalid",
            "SAM3 video mask export requires a video track result.",
        )

    normalized_metadata_path = _safe_relative_path(
        metadata_path,
        code="video_mask_export_metadata_path_unsafe",
        message="SAM3 video mask export requires a safe metadata path.",
    )
    normalized_package_id = _optional_id(package_id) or f"{result.track_id}_video_mask_package"

    files: list[dict[str, object]] = []
    seen_paths: set[str] = set()

    _append_file(
        files,
        seen_paths,
        {
            "path": normalized_metadata_path,
            "role": "manifest",
            "kind": "json",
        },
    )
    _append_file(
        files,
        seen_paths,
        {
            "path": "qa/video_track_scorecard.json",
            "role": "qa",
            "kind": "json",
        },
    )
    _append_file(
        files,
        seen_paths,
        {
            "path": "lineage.json",
            "role": "lineage",
            "kind": "json",
        },
    )

    frame_mask_assets = _frame_mask_asset_entries(result.frames)
    preview_assets = _preview_asset_entries(result.frames) if include_previews else []
    for entry in frame_mask_assets:
        _append_file(files, seen_paths, _asset_file_entry(entry, "mask_frame", "mask_png"))
    for entry in preview_assets:
        _append_file(files, seen_paths, _asset_file_entry(entry, "preview", "preview_webp"))

    qa_scorecard = analyze_video_track_result(result).to_dict()
    lineage = _lineage(result, frame_mask_assets, preview_assets)
    manifest = _manifest(
        result,
        package_id=normalized_package_id,
        metadata_path=normalized_metadata_path,
        frame_mask_assets=frame_mask_assets,
        preview_assets=preview_assets,
        qa_path="qa/video_track_scorecard.json",
        lineage_path="lineage.json",
    )

    payload: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_MASK_EXPORT_PAYLOAD_SCHEMA_VERSION,
        "packageId": normalized_package_id,
        "trackId": result.track_id,
        "metadataPath": normalized_metadata_path,
        "manifest": manifest,
        "qaScorecard": qa_scorecard,
        "lineage": lineage,
        "files": files,
        "blockedReasonCodes": [],
    }
    return dict(scrub_sam3_metadata(payload))


def _manifest(
    result: Sam3VideoTrackResult,
    *,
    package_id: str,
    metadata_path: str,
    frame_mask_assets: list[dict[str, object]],
    preview_assets: list[dict[str, object]],
    qa_path: str,
    lineage_path: str,
) -> dict[str, object]:
    track_result = result.to_dict()
    track_result["packagePath"] = metadata_path
    return dict(
        scrub_sam3_metadata(
            {
                "schemaVersion": SAM3_VIDEO_MASK_PACKAGE_SCHEMA_VERSION,
                "packageId": package_id,
                "trackId": result.track_id,
                "sourceVideoAssetId": result.source_video_asset_id,
                "fps": result.fps,
                "width": result.width,
                "height": result.height,
                "objectCount": len(result.objects),
                "frameCount": len(result.frames),
                "objects": [item.to_dict() for item in result.objects],
                "frameMaskAssets": frame_mask_assets,
                "previewAssets": preview_assets,
                "metadataPath": metadata_path,
                "qaPath": qa_path,
                "lineagePath": lineage_path,
                "trackResult": track_result,
            }
        )
    )


def _lineage(
    result: Sam3VideoTrackResult,
    frame_mask_assets: list[dict[str, object]],
    preview_assets: list[dict[str, object]],
) -> dict[str, object]:
    mask_asset_ids = sorted(
        {
            str(entry["assetId"])
            for entry in frame_mask_assets
            if _optional_id(entry.get("assetId")) is not None
        }
    )
    preview_asset_ids = sorted(
        {
            str(entry["assetId"])
            for entry in preview_assets
            if _optional_id(entry.get("assetId")) is not None
        }
    )
    return {
        "schemaVersion": SAM3_VIDEO_TRACK_LINEAGE_SCHEMA_VERSION,
        "trackId": result.track_id,
        "sourceVideoAssetId": result.source_video_asset_id,
        "maskAssetIds": mask_asset_ids,
        "previewAssetIds": preview_asset_ids,
    }


def _frame_mask_asset_entries(
    frames: tuple[Sam3VideoMaskFrame, ...],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for frame in frames:
        frame_name = _frame_name(frame.frame_index)
        for object_id, asset_id in frame.object_masks.items():
            path = f"frames/{frame_name}_object_{_path_name(object_id)}.png"
            entries.append(
                {
                    "frameIndex": frame.frame_index,
                    "timestampMs": frame.timestamp_ms,
                    "objectId": object_id,
                    "assetId": asset_id,
                    "path": path,
                }
            )
    return entries


def _preview_asset_entries(
    frames: tuple[Sam3VideoMaskFrame, ...],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for frame in frames:
        asset_id = _optional_id(frame.preview_asset_id)
        if asset_id is None:
            continue
        frame_name = _frame_name(frame.frame_index)
        entries.append(
            {
                "frameIndex": frame.frame_index,
                "timestampMs": frame.timestamp_ms,
                "assetId": asset_id,
                "path": f"previews/{frame_name}_overlay.webp",
            }
        )
    return entries


def _asset_file_entry(
    entry: Mapping[str, object],
    role: str,
    kind: str,
) -> dict[str, object]:
    file_entry = {
        "path": _required_text(entry.get("path")),
        "role": role,
        "kind": kind,
        "assetId": _required_text(entry.get("assetId")),
        "frameIndex": int(entry.get("frameIndex") or 0),
        "timestampMs": int(entry.get("timestampMs") or 0),
    }
    object_id = _optional_id(entry.get("objectId"))
    if object_id is not None:
        file_entry["objectId"] = object_id
    return file_entry


def _append_file(
    files: list[dict[str, object]],
    seen_paths: set[str],
    entry: dict[str, object],
) -> None:
    path = _safe_relative_path(
        entry.get("path"),
        code="video_mask_export_duplicate_path",
        message="SAM3 video mask export path must be package-relative.",
    )
    if path in seen_paths:
        raise Sam3VideoMaskExportError(
            "video_mask_export_duplicate_path",
            "SAM3 video mask export paths must be unique.",
        )
    seen_paths.add(path)
    entry["path"] = path
    files.append(entry)


def _frame_name(frame_index: int) -> str:
    return f"frame_{max(0, int(frame_index)) + 1:06d}"


def _path_name(value: object) -> str:
    text = str(value or "").strip()
    slug = "".join(char if char in _SAFE_NAME_CHARS else "_" for char in text).strip("._-")
    if not slug or slug in {".", ".."}:
        return "object"
    return slug[:96]


def _safe_relative_path(value: object, *, code: str, message: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    lowered = text.lower()
    if not text:
        raise Sam3VideoMaskExportError(code, message)
    if "\0" in text or "://" in text or lowered.startswith(("http:", "https:", "file:")):
        raise Sam3VideoMaskExportError(code, message)
    if text.startswith("/") or text.startswith("//") or ":" in text:
        raise Sam3VideoMaskExportError(code, message)
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise Sam3VideoMaskExportError(code, message)
    return "/".join(parts)


def _required_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise Sam3VideoMaskExportError(
            "video_mask_export_result_invalid",
            "SAM3 video mask export requires non-empty file fields.",
        )
    return text


def _optional_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "SAM3_VIDEO_MASK_EXPORT_PAYLOAD_SCHEMA_VERSION",
    "SAM3_VIDEO_MASK_PACKAGE_SCHEMA_VERSION",
    "SAM3_VIDEO_TRACK_LINEAGE_SCHEMA_VERSION",
    "SAM3_VIDEO_TRACK_QA_SCORECARD_SCHEMA_VERSION",
    "Sam3VideoMaskExportError",
    "build_video_mask_package_export_payload",
]
