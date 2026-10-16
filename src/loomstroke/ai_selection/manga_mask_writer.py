"""File writer for SAM3 comic mask package exports."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .manga_mask_export import (
    Sam3ComicMaskExportError,
    build_comic_mask_package_export_payload,
)
from .manga_mask_package import Sam3ComicMaskPackage
from .metadata_scrub import scrub_sam3_metadata

SAM3_COMIC_MASK_EXPORT_WRITE_RESULT_SCHEMA_VERSION = "sam3.comic_mask_export_write_result.v1"


class Sam3ComicMaskPackageWriterError(ValueError):
    """Raised when a comic mask package cannot be written safely."""

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


def write_comic_mask_package_export(
    package: Sam3ComicMaskPackage,
    output_dir: str | Path,
    asset_payloads: Mapping[str, bytes | bytearray | memoryview],
    *,
    allowed_roots: Sequence[str | Path],
) -> dict[str, object]:
    """Write a SAM3 comic mask package under an allowed root."""

    try:
        export_payload = build_comic_mask_package_export_payload(package)
    except Sam3ComicMaskExportError as exc:
        raise Sam3ComicMaskPackageWriterError(exc.code, str(exc)) from exc

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
            written_files.append(summary)
    except OSError as exc:
        raise Sam3ComicMaskPackageWriterError(
            "comic_mask_export_write_failed",
            "SAM3 comic mask package export failed.",
        ) from exc

    result: dict[str, object] = {
        "schemaVersion": SAM3_COMIC_MASK_EXPORT_WRITE_RESULT_SCHEMA_VERSION,
        "status": "written",
        "packageId": str(export_payload.get("packageId") or ""),
        "metadataPath": str(export_payload.get("metadataPath") or ""),
        "fileCount": len(written_files),
        "assetCount": sum(1 for entry in entries if entry.role == "export_asset"),
        "files": written_files,
        "blockedReasonCodes": [],
    }
    return dict(scrub_sam3_metadata(result))


def _build_write_entries(
    export_payload: Mapping[str, object],
    output_root: Path,
    asset_payloads: Mapping[str, bytes | bytearray | memoryview],
) -> list[_WriteEntry]:
    files = _file_summaries(export_payload.get("files"))
    manifest = _mapping(export_payload.get("manifest"))
    if manifest is None:
        raise Sam3ComicMaskPackageWriterError(
            "comic_mask_export_target_path_unsafe",
            "SAM3 comic mask export payload requires a manifest.",
        )

    asset_entries: list[_WriteEntry] = []
    manifest_entry: _WriteEntry | None = None
    for file_summary in files:
        package_path = _required_text(file_summary.get("path"))
        role = _required_text(file_summary.get("role"))
        kind = _required_text(file_summary.get("kind"))
        target_path = _target_for_package_path(output_root, package_path)
        if role == "manifest":
            manifest_entry = _WriteEntry(
                package_path=package_path,
                role=role,
                kind=kind,
                target_path=target_path,
                payload=_json_payload_bytes(manifest),
            )
            continue
        if role != "export_asset":
            raise Sam3ComicMaskPackageWriterError(
                "comic_mask_export_target_path_unsafe",
                "SAM3 comic mask export payload has an unsupported file role.",
            )
        asset_id = _required_text(file_summary.get("assetId"))
        asset_entries.append(
            _WriteEntry(
                package_path=package_path,
                role=role,
                kind=kind,
                target_path=target_path,
                payload=_asset_payload(asset_payloads, asset_id),
                asset_id=asset_id,
            )
        )

    if manifest_entry is None:
        raise Sam3ComicMaskPackageWriterError(
            "comic_mask_export_target_path_unsafe",
            "SAM3 comic mask export payload requires a manifest file entry.",
        )
    return [*asset_entries, manifest_entry]


def _resolve_output_root(output_dir: str | Path, roots: Sequence[str | Path]) -> Path:
    normalized_roots = _normalize_roots(roots)
    if not normalized_roots:
        raise Sam3ComicMaskPackageWriterError(
            "comic_mask_export_roots_required",
            "SAM3 comic mask package export requires at least one allowed root.",
        )

    raw = Path(output_dir).expanduser()
    if _has_unsafe_output_part(raw):
        raise Sam3ComicMaskPackageWriterError(
            "comic_mask_export_output_root_outside_allowed_roots",
            "SAM3 comic mask package output directory must be under an allowed root.",
        )
    candidates = [raw] if raw.is_absolute() else [root / raw for root in normalized_roots]
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if any(_is_relative_to(resolved, root) for root in normalized_roots):
            return resolved
    raise Sam3ComicMaskPackageWriterError(
        "comic_mask_export_output_root_outside_allowed_roots",
        "SAM3 comic mask package output directory must be under an allowed root.",
    )


def _normalize_roots(roots: Sequence[str | Path]) -> tuple[Path, ...]:
    normalized: list[Path] = []
    for root in roots:
        try:
            normalized.append(Path(root).expanduser().resolve(strict=False))
        except OSError as exc:
            raise Sam3ComicMaskPackageWriterError(
                "comic_mask_export_output_root_outside_allowed_roots",
                "SAM3 comic mask package export root is invalid.",
            ) from exc
    return tuple(dict.fromkeys(normalized))


def _target_for_package_path(output_root: Path, package_path: str) -> Path:
    if "\0" in package_path:
        raise Sam3ComicMaskPackageWriterError(
            "comic_mask_export_target_path_unsafe",
            "SAM3 comic mask package path is unsafe.",
        )
    parts = package_path.replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise Sam3ComicMaskPackageWriterError(
            "comic_mask_export_target_path_unsafe",
            "SAM3 comic mask package path is unsafe.",
        )
    target = output_root.joinpath(*parts).resolve(strict=False)
    if not _is_relative_to(target, output_root):
        raise Sam3ComicMaskPackageWriterError(
            "comic_mask_export_target_path_unsafe",
            "SAM3 comic mask package path escapes the output directory.",
        )
    return target


def _asset_payload(
    asset_payloads: Mapping[str, bytes | bytearray | memoryview],
    asset_id: str,
) -> bytes:
    if asset_id not in asset_payloads:
        raise Sam3ComicMaskPackageWriterError(
            "comic_mask_export_asset_payload_missing",
            "SAM3 comic mask package export is missing an asset payload.",
        )
    value = asset_payloads[asset_id]
    if isinstance(value, memoryview):
        payload = value.tobytes()
    elif isinstance(value, (bytes, bytearray)):
        payload = bytes(value)
    else:
        raise Sam3ComicMaskPackageWriterError(
            "comic_mask_export_asset_payload_invalid",
            "SAM3 comic mask package asset payload must be bytes-like.",
        )
    if not payload:
        raise Sam3ComicMaskPackageWriterError(
            "comic_mask_export_asset_payload_invalid",
            "SAM3 comic mask package asset payload must not be empty.",
        )
    return payload


def _file_summaries(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise Sam3ComicMaskPackageWriterError(
            "comic_mask_export_target_path_unsafe",
            "SAM3 comic mask export payload requires file entries.",
        )
    summaries: list[Mapping[str, object]] = []
    for item in value:
        summary = _mapping(item)
        if summary is None:
            raise Sam3ComicMaskPackageWriterError(
                "comic_mask_export_target_path_unsafe",
                "SAM3 comic mask export payload has invalid file entries.",
            )
        summaries.append(summary)
    return summaries


def _required_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise Sam3ComicMaskPackageWriterError(
            "comic_mask_export_target_path_unsafe",
            "SAM3 comic mask export payload contains an empty file field.",
        )
    return text


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


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
    "SAM3_COMIC_MASK_EXPORT_WRITE_RESULT_SCHEMA_VERSION",
    "Sam3ComicMaskPackageWriterError",
    "write_comic_mask_package_export",
]
