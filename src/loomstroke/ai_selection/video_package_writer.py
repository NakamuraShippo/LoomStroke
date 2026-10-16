"""File writer for SAM3 video mask package exports."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .metadata_scrub import scrub_sam3_metadata
from .video_package_export import (
    Sam3VideoMaskExportError,
    build_video_mask_package_export_payload,
)
from .video_tracker_model import Sam3VideoTrackResult

SAM3_VIDEO_MASK_EXPORT_WRITE_RESULT_SCHEMA_VERSION = "sam3.video_mask_export_write_result.v1"

_ASSET_ROLES = {"mask_frame", "preview"}
_JSON_PAYLOAD_BY_ROLE = {
    "manifest": "manifest",
    "qa": "qaScorecard",
    "lineage": "lineage",
}


class Sam3VideoMaskPackageWriterError(ValueError):
    """Raised when a video mask package cannot be written safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class _WriteEntry:
    package_path: str
    role: str
    kind: str
    target_path: Path
    payload: bytes
    asset_id: str | None = None
    frame_index: int | None = None
    object_id: str | None = None


def write_video_mask_package_export(
    result: Sam3VideoTrackResult,
    output_dir: str | Path,
    asset_payloads: Mapping[str, bytes | bytearray | memoryview],
    *,
    allowed_roots: Sequence[str | Path],
    package_id: str | None = None,
    metadata_path: str = "video_mask_package.json",
    include_previews: bool = True,
) -> dict[str, object]:
    """Write a SAM3 video mask package under an allowed root."""

    try:
        export_payload = build_video_mask_package_export_payload(
            result,
            package_id=package_id,
            metadata_path=metadata_path,
            include_previews=include_previews,
        )
    except Sam3VideoMaskExportError as exc:
        raise Sam3VideoMaskPackageWriterError(exc.code, str(exc)) from exc

    output_root = _resolve_output_root(output_dir, allowed_roots)
    entries = _build_write_entries(export_payload, output_root, asset_payloads)

    written_files: list[dict[str, object]] = []
    try:
        for entry in entries:
            _atomic_write_bytes(entry.target_path, entry.payload)
            summary: dict[str, object] = {
                "path": entry.package_path,
                "role": entry.role,
                "kind": entry.kind,
                "bytesWritten": len(entry.payload),
            }
            if entry.asset_id is not None:
                summary["assetId"] = entry.asset_id
            if entry.frame_index is not None:
                summary["frameIndex"] = entry.frame_index
            if entry.object_id is not None:
                summary["objectId"] = entry.object_id
            written_files.append(summary)
    except OSError as exc:
        raise Sam3VideoMaskPackageWriterError(
            "video_mask_export_write_failed",
            "SAM3 video mask package export failed.",
        ) from exc

    result_payload: dict[str, object] = {
        "schemaVersion": SAM3_VIDEO_MASK_EXPORT_WRITE_RESULT_SCHEMA_VERSION,
        "status": "written",
        "packageId": str(export_payload.get("packageId") or ""),
        "trackId": str(export_payload.get("trackId") or ""),
        "metadataPath": str(export_payload.get("metadataPath") or ""),
        "fileCount": len(written_files),
        "assetCount": sum(1 for entry in entries if entry.role in _ASSET_ROLES),
        "files": written_files,
        "blockedReasonCodes": [],
    }
    return dict(scrub_sam3_metadata(result_payload))


def _build_write_entries(
    export_payload: Mapping[str, object],
    output_root: Path,
    asset_payloads: Mapping[str, bytes | bytearray | memoryview],
) -> list[_WriteEntry]:
    files = _file_summaries(export_payload.get("files"))
    entries: list[_WriteEntry] = []
    for file_summary in files:
        package_path = _required_text(file_summary.get("path"))
        role = _required_text(file_summary.get("role"))
        kind = _required_text(file_summary.get("kind"))
        target_path = _target_for_package_path(output_root, package_path)

        if role in _JSON_PAYLOAD_BY_ROLE:
            entries.append(
                _WriteEntry(
                    package_path=package_path,
                    role=role,
                    kind=kind,
                    target_path=target_path,
                    payload=_json_role_payload_bytes(export_payload, role),
                )
            )
            continue

        if role not in _ASSET_ROLES:
            raise Sam3VideoMaskPackageWriterError(
                "video_mask_export_target_path_unsafe",
                "SAM3 video mask export payload has an unsupported file role.",
            )
        asset_id = _required_text(file_summary.get("assetId"))
        entries.append(
            _WriteEntry(
                package_path=package_path,
                role=role,
                kind=kind,
                target_path=target_path,
                payload=_asset_payload(asset_payloads, asset_id),
                asset_id=asset_id,
                frame_index=_optional_int(file_summary.get("frameIndex")),
                object_id=_optional_id(file_summary.get("objectId")),
            )
        )
    return entries


def _json_role_payload_bytes(
    export_payload: Mapping[str, object],
    role: str,
) -> bytes:
    key = _JSON_PAYLOAD_BY_ROLE[role]
    payload = _mapping(export_payload.get(key))
    if payload is None:
        raise Sam3VideoMaskPackageWriterError(
            "video_mask_export_target_path_unsafe",
            "SAM3 video mask export payload is missing a JSON section.",
        )
    return _json_payload_bytes(payload)


def _resolve_output_root(output_dir: str | Path, roots: Sequence[str | Path]) -> Path:
    normalized_roots = _normalize_roots(roots)
    if not normalized_roots:
        raise Sam3VideoMaskPackageWriterError(
            "video_mask_export_roots_required",
            "SAM3 video mask package export requires at least one allowed root.",
        )

    raw = Path(output_dir).expanduser()
    if _has_unsafe_output_part(raw):
        raise Sam3VideoMaskPackageWriterError(
            "video_mask_export_output_root_outside_allowed_roots",
            "SAM3 video mask package output directory must be under an allowed root.",
        )
    candidates = [raw] if raw.is_absolute() else [root / raw for root in normalized_roots]
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if any(_is_relative_to(resolved, root) for root in normalized_roots):
            return resolved
    raise Sam3VideoMaskPackageWriterError(
        "video_mask_export_output_root_outside_allowed_roots",
        "SAM3 video mask package output directory must be under an allowed root.",
    )


def _normalize_roots(roots: Sequence[str | Path]) -> tuple[Path, ...]:
    normalized: list[Path] = []
    for root in roots:
        try:
            normalized.append(Path(root).expanduser().resolve(strict=False))
        except OSError as exc:
            raise Sam3VideoMaskPackageWriterError(
                "video_mask_export_output_root_outside_allowed_roots",
                "SAM3 video mask package export root is invalid.",
            ) from exc
    return tuple(dict.fromkeys(normalized))


def _target_for_package_path(output_root: Path, package_path: str) -> Path:
    if "\0" in package_path:
        raise Sam3VideoMaskPackageWriterError(
            "video_mask_export_target_path_unsafe",
            "SAM3 video mask package path is unsafe.",
        )
    text = package_path.replace("\\", "/")
    lowered = text.lower()
    if "://" in text or lowered.startswith(("http:", "https:", "file:")):
        raise Sam3VideoMaskPackageWriterError(
            "video_mask_export_target_path_unsafe",
            "SAM3 video mask package path is unsafe.",
        )
    if text.startswith("/") or text.startswith("//"):
        raise Sam3VideoMaskPackageWriterError(
            "video_mask_export_target_path_unsafe",
            "SAM3 video mask package path is unsafe.",
        )
    parts = text.split("/")
    if any(part in {"", ".", ".."} or ":" in part for part in parts):
        raise Sam3VideoMaskPackageWriterError(
            "video_mask_export_target_path_unsafe",
            "SAM3 video mask package path is unsafe.",
        )
    target = output_root.joinpath(*parts).resolve(strict=False)
    if not _is_relative_to(target, output_root):
        raise Sam3VideoMaskPackageWriterError(
            "video_mask_export_target_path_unsafe",
            "SAM3 video mask package path escapes the output directory.",
        )
    return target


def _asset_payload(
    asset_payloads: Mapping[str, bytes | bytearray | memoryview],
    asset_id: str,
) -> bytes:
    if asset_id not in asset_payloads:
        raise Sam3VideoMaskPackageWriterError(
            "video_mask_export_asset_payload_missing",
            "SAM3 video mask package export is missing an asset payload.",
        )
    value = asset_payloads[asset_id]
    if isinstance(value, memoryview):
        payload = value.tobytes()
    elif isinstance(value, (bytes, bytearray)):
        payload = bytes(value)
    else:
        raise Sam3VideoMaskPackageWriterError(
            "video_mask_export_asset_payload_invalid",
            "SAM3 video mask package asset payload must be bytes-like.",
        )
    if not payload:
        raise Sam3VideoMaskPackageWriterError(
            "video_mask_export_asset_payload_invalid",
            "SAM3 video mask package asset payload must not be empty.",
        )
    return payload


def _file_summaries(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise Sam3VideoMaskPackageWriterError(
            "video_mask_export_target_path_unsafe",
            "SAM3 video mask export payload requires file entries.",
        )
    summaries: list[Mapping[str, object]] = []
    for item in value:
        summary = _mapping(item)
        if summary is None:
            raise Sam3VideoMaskPackageWriterError(
                "video_mask_export_target_path_unsafe",
                "SAM3 video mask export payload has invalid file entries.",
            )
        summaries.append(summary)
    return summaries


def _required_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise Sam3VideoMaskPackageWriterError(
            "video_mask_export_target_path_unsafe",
            "SAM3 video mask export payload contains an empty file field.",
        )
    return text


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _optional_id(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _has_unsafe_output_part(path: Path) -> bool:
    text = str(path)
    if "\0" in text:
        return True
    if not path.is_absolute() and any(":" in part for part in path.parts):
        return True
    return any(part in {"", ".", ".."} for part in path.parts)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath([str(path), str(root)])
    except ValueError:
        return False
    return os.path.normcase(common) == os.path.normcase(str(root))


def _json_payload_bytes(payload: Mapping[str, object]) -> bytes:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    return f"{text}\n".encode()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    tmp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path_text = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=str(path.parent))
        tmp_path = Path(tmp_path_text)
        with os.fdopen(tmp_fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
        raise


__all__ = [
    "SAM3_VIDEO_MASK_EXPORT_WRITE_RESULT_SCHEMA_VERSION",
    "Sam3VideoMaskPackageWriterError",
    "write_video_mask_package_export",
]
