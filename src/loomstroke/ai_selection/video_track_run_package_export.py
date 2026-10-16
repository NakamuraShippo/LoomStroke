"""Export payload helpers for SAM3 video track run packages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .metadata_scrub import scrub_sam3_metadata
from .video_qa import analyze_video_track_result
from .video_track_run import Sam3VideoTrackRunResult
from .video_tracker_model import Sam3VideoMaskFrame

SAM3_VIDEO_TRACK_RUN_PACKAGE_SCHEMA_VERSION = "sam3.video_track_run_package.v1"
SAM3_VIDEO_TRACK_RUN_EXPORT_PAYLOAD_SCHEMA_VERSION = "sam3.video_track_run_export_payload.v1"
SAM3_VIDEO_TRACK_RUN_LINEAGE_SCHEMA_VERSION = "sam3.video_track_run_lineage.v1"

_SAFE_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789" "._-"
)
_REDACTED = "<redacted>"


class Sam3VideoTrackRunPackageExportError(ValueError):
    """Raised when a video track run package export payload cannot be built."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_video_track_run_package_export_payload(
    run_result: Sam3VideoTrackRunResult,
    *,
    package_id: str | None = None,
    metadata_path: str = "video_track_run_package.json",
    include_previews: bool = True,
) -> dict[str, object]:
    """Build a file-write-free export payload for a SAM3 video track run package."""

    if not isinstance(run_result, Sam3VideoTrackRunResult):
        raise Sam3VideoTrackRunPackageExportError(
            "video_track_run_export_result_invalid",
            "SAM3 video track run package export requires a video track run result.",
        )

    normalized_metadata_path = _safe_relative_path(
        metadata_path,
        code="video_track_run_export_metadata_path_unsafe",
        message="SAM3 video track run package export requires a safe metadata path.",
    )
    normalized_package_id = _optional_id(package_id) or _default_package_id(run_result)

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
            "path": "run/video_track_run_result.json",
            "role": "run_result",
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

    track_result = run_result.track_result
    frame_mask_assets: list[dict[str, object]] = []
    preview_assets: list[dict[str, object]] = []
    qa_scorecard: dict[str, object] | None = None
    if track_result is not None:
        _append_file(
            files,
            seen_paths,
            {
                "path": "qa/video_track_scorecard.json",
                "role": "qa",
                "kind": "json",
            },
        )
        frame_mask_assets = _frame_mask_asset_entries(track_result.frames)
        preview_assets = _preview_asset_entries(track_result.frames) if include_previews else []
        for entry in frame_mask_assets:
            _append_file(files, seen_paths, _asset_file_entry(entry, "mask_frame", "mask_png"))
        for entry in preview_assets:
            _append_file(files, seen_paths, _asset_file_entry(entry, "preview", "preview_webp"))
        qa_scorecard = analyze_video_track_result(track_result).to_dict()

    incomplete_reason_codes = _incomplete_reason_codes(run_result)
    blocked_reason_codes = _blocked_reason_codes(run_result)
    run_payload = _run_result_payload(run_result, metadata_path=normalized_metadata_path)
    lineage = _lineage(run_result, frame_mask_assets, preview_assets)
    manifest = _manifest(
        run_result,
        package_id=normalized_package_id,
        metadata_path=normalized_metadata_path,
        frame_mask_assets=frame_mask_assets,
        preview_assets=preview_assets,
        qa_path="qa/video_track_scorecard.json" if track_result is not None else None,
        lineage_path="lineage.json",
        run_result_path="run/video_track_run_result.json",
    )

    payload: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_RUN_EXPORT_PAYLOAD_SCHEMA_VERSION,
        "packageId": normalized_package_id,
        "requestId": run_result.request.request_id,
        "sourceManifestId": run_result.request.source_manifest.manifest_id,
        "trackId": track_result.track_id if track_result is not None else None,
        "runStatus": run_result.status,
        "completed": run_result.completed,
        "incomplete": not run_result.completed,
        "partial": run_result.partial,
        "metadataPath": normalized_metadata_path,
        "manifest": manifest,
        "runResult": run_payload,
        "qaScorecard": qa_scorecard,
        "lineage": lineage,
        "files": files,
        "blockedReasonCodes": blocked_reason_codes,
        "incompleteReasonCodes": incomplete_reason_codes,
    }
    return _scrubbed_payload(payload)


def _manifest(
    run_result: Sam3VideoTrackRunResult,
    *,
    package_id: str,
    metadata_path: str,
    frame_mask_assets: list[dict[str, object]],
    preview_assets: list[dict[str, object]],
    qa_path: str | None,
    lineage_path: str,
    run_result_path: str,
) -> dict[str, object]:
    request = run_result.request
    track_result = run_result.track_result
    track_result_payload: dict[str, object] | None = None
    if track_result is not None:
        track_result_payload = track_result.to_dict()
        track_result_payload["packagePath"] = metadata_path

    payload: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_RUN_PACKAGE_SCHEMA_VERSION,
        "packageId": package_id,
        "requestId": request.request_id,
        "sourceManifestId": request.source_manifest.manifest_id,
        "sourceAssetId": request.source_manifest.source_asset_id,
        "trackId": track_result.track_id if track_result is not None else None,
        "runStatus": run_result.status,
        "completed": run_result.completed,
        "incomplete": not run_result.completed,
        "partial": run_result.partial,
        "expectedFrameCount": len(request.source_manifest.frames),
        "partialFrameCount": run_result.partial_frame_count,
        "frameCount": len(track_result.frames) if track_result is not None else 0,
        "objectCount": len(request.objects),
        "metadataPath": metadata_path,
        "runResultPath": run_result_path,
        "qaPath": qa_path,
        "lineagePath": lineage_path,
        "frameMaskAssets": frame_mask_assets,
        "previewAssets": preview_assets,
        "request": request.to_dict(),
        "trackResult": track_result_payload,
        "blockedReasons": [reason.to_dict() for reason in run_result.blocked_reasons],
        "warnings": [warning.to_dict() for warning in run_result.warnings],
        "recommendedActions": list(run_result.recommended_actions),
    }
    return _scrubbed_payload(payload)


def _lineage(
    run_result: Sam3VideoTrackRunResult,
    frame_mask_assets: list[dict[str, object]],
    preview_assets: list[dict[str, object]],
) -> dict[str, object]:
    track_result = run_result.track_result
    payload: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_TRACK_RUN_LINEAGE_SCHEMA_VERSION,
        "requestId": run_result.request.request_id,
        "sourceManifestId": run_result.request.source_manifest.manifest_id,
        "sourceAssetId": run_result.request.source_manifest.source_asset_id,
        "trackId": track_result.track_id if track_result is not None else None,
        "runStatus": run_result.status,
        "completed": run_result.completed,
        "incomplete": not run_result.completed,
        "partial": run_result.partial,
        "maskAssetIds": _asset_ids(frame_mask_assets),
        "previewAssetIds": _asset_ids(preview_assets),
    }
    return _scrubbed_payload(payload)


def _run_result_payload(
    run_result: Sam3VideoTrackRunResult,
    *,
    metadata_path: str,
) -> dict[str, object]:
    payload = run_result.to_dict()
    track_result = payload.get("trackResult")
    if isinstance(track_result, dict):
        track_result["packagePath"] = metadata_path
    return _scrubbed_payload(payload)


def _frame_mask_asset_entries(
    frames: tuple[Sam3VideoMaskFrame, ...],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for frame in frames:
        frame_name = _frame_name(frame.frame_index)
        for object_id, asset_id in frame.object_masks.items():
            entries.append(
                {
                    "frameIndex": frame.frame_index,
                    "timestampMs": frame.timestamp_ms,
                    "objectId": object_id,
                    "assetId": asset_id,
                    "path": f"frames/{frame_name}_object_{_path_name(object_id)}.png",
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
        entries.append(
            {
                "frameIndex": frame.frame_index,
                "timestampMs": frame.timestamp_ms,
                "assetId": asset_id,
                "path": f"previews/{_frame_name(frame.frame_index)}_overlay.webp",
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
        code="video_track_run_export_duplicate_path",
        message="SAM3 video track run package export path must be package-relative.",
    )
    if path in seen_paths:
        raise Sam3VideoTrackRunPackageExportError(
            "video_track_run_export_duplicate_path",
            "SAM3 video track run package export paths must be unique.",
        )
    seen_paths.add(path)
    entry["path"] = path
    files.append(entry)


def _asset_ids(entries: list[dict[str, object]]) -> list[str]:
    return sorted(
        {
            str(entry["assetId"])
            for entry in entries
            if _optional_id(entry.get("assetId")) is not None
        }
    )


def _incomplete_reason_codes(run_result: Sam3VideoTrackRunResult) -> list[str]:
    if run_result.completed:
        return []
    codes = [f"video_track_run_{run_result.status}"]
    if run_result.partial:
        codes.append("video_track_run_partial")
    return codes


def _blocked_reason_codes(run_result: Sam3VideoTrackRunResult) -> list[str]:
    return sorted(
        {
            reason.code
            for reason in run_result.blocked_reasons
            if _optional_id(reason.code) is not None
        }
    )


def _default_package_id(run_result: Sam3VideoTrackRunResult) -> str:
    if run_result.track_result is not None:
        return f"{run_result.track_result.track_id}_video_track_run_package"
    return f"{run_result.request.request_id}_video_track_run_package"


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
        raise Sam3VideoTrackRunPackageExportError(code, message)
    if "\0" in text or "://" in text or lowered.startswith(("http:", "https:", "file:")):
        raise Sam3VideoTrackRunPackageExportError(code, message)
    if text.startswith("/") or text.startswith("//") or ":" in text:
        raise Sam3VideoTrackRunPackageExportError(code, message)
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise Sam3VideoTrackRunPackageExportError(code, message)
    return "/".join(parts)


def _required_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise Sam3VideoTrackRunPackageExportError(
            "video_track_run_export_file_field_invalid",
            "SAM3 video track run package export requires non-empty file fields.",
        )
    return text


def _optional_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _scrubbed_payload(value: Mapping[str, object]) -> dict[str, object]:
    scrubbed = scrub_sam3_metadata(value)
    redacted = _redact_source_locations(scrubbed)
    return dict(redacted) if isinstance(redacted, Mapping) else {}


def _redact_source_locations(value: Any) -> Any:
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
    "SAM3_VIDEO_TRACK_RUN_EXPORT_PAYLOAD_SCHEMA_VERSION",
    "SAM3_VIDEO_TRACK_RUN_LINEAGE_SCHEMA_VERSION",
    "SAM3_VIDEO_TRACK_RUN_PACKAGE_SCHEMA_VERSION",
    "Sam3VideoTrackRunPackageExportError",
    "build_video_track_run_package_export_payload",
]
