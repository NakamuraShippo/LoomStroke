"""Credential-free SAM3 asset persistence."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .data_model import (
    AssetRef,
    MaskAsset,
    MaskAssetKind,
    MaskCandidateSet,
    Sam3JobStatus,
    SourceSnapshotRef,
)
from .metadata_scrub import scrub_sam3_metadata

SAM3_ASSET_SCHEMA_VERSION = "sam3.asset.v1"
SAM3_CANDIDATE_SET_SCHEMA_VERSION = "sam3.candidate_set.v1"
SAM3_JOB_SCHEMA_VERSION = "sam3.job.v1"
SAM3_SOURCE_SNAPSHOT_SCHEMA_VERSION = "sam3.source_snapshot.v1"

_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_MANIFEST_FILENAME = "manifest.json"

logger = logging.getLogger(__name__)


class Sam3AssetStore:
    """Store SAM3 mask assets and metadata under ``generated_assets/sam3``."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or _default_asset_root()).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root_path(self) -> Path:
        return self._root

    def save_mask_asset(
        self,
        mask_png: bytes,
        *,
        asset_id: str,
        kind: MaskAssetKind,
        width: int,
        height: int,
        bbox: tuple[int, int, int, int],
        content_hash: str | None = None,
        source_candidate_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> MaskAsset:
        asset_id = _validate_id("asset_id", asset_id)
        asset_root = self._mask_root(asset_id)
        artifact_path = self._guard_path(asset_root / "mask.png")
        artifact_sha = _sha256_bytes(mask_png)
        relative_path = self._relative_path(artifact_path)
        asset = MaskAsset(
            asset_id=asset_id,
            kind=kind,
            width=int(width),
            height=int(height),
            relative_path=relative_path,
            content_hash=content_hash or artifact_sha,
            bbox=tuple(int(item) for item in bbox),
            source_candidate_id=source_candidate_id,
            metadata=dict(scrub_sam3_metadata(dict(metadata or {}))),
        )

        _atomic_write_bytes(artifact_path, mask_png)
        self._write_asset_manifest(
            asset_root / _MANIFEST_FILENAME,
            asset,
            len(mask_png),
            artifact_sha,
        )
        return asset

    def save_preview_asset(
        self,
        preview_png: bytes,
        *,
        asset_id: str,
        width: int,
        height: int,
        bbox: tuple[int, int, int, int],
        content_hash: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AssetRef:
        asset_id = _validate_id("asset_id", asset_id)
        asset_root = self._preview_root(asset_id)
        artifact_path = self._guard_path(asset_root / "preview.png")
        artifact_sha = _sha256_bytes(preview_png)
        relative_path = self._relative_path(artifact_path)
        asset = AssetRef(
            asset_id=asset_id,
            uri=relative_path,
            kind="preview_png",
            width=int(width),
            height=int(height),
            content_hash=content_hash or artifact_sha,
            bbox=tuple(int(item) for item in bbox),
            metadata=dict(scrub_sam3_metadata(dict(metadata or {}))),
        )

        _atomic_write_bytes(artifact_path, preview_png)
        self._write_asset_manifest(
            asset_root / _MANIFEST_FILENAME,
            asset,
            len(preview_png),
            artifact_sha,
        )
        return asset

    def load_mask_asset(self, asset_id: str) -> MaskAsset:
        manifest = self._read_asset_manifest(self._mask_manifest_path(asset_id))
        asset = MaskAsset.from_dict(_dict_field(manifest, "asset"))
        _validate_id("asset_id", asset.asset_id)
        if asset.asset_id != asset_id:
            raise ValueError(f"SAM3 mask asset id mismatch: {asset.asset_id!r} != {asset_id!r}")
        self.mask_asset_path(asset)
        return asset

    def load_preview_asset(self, asset_id: str) -> AssetRef:
        manifest = self._read_asset_manifest(self._preview_manifest_path(asset_id))
        asset = AssetRef.from_dict(_dict_field(manifest, "asset"))
        _validate_id("asset_id", asset.asset_id)
        if asset.asset_id != asset_id:
            raise ValueError(f"SAM3 preview asset id mismatch: {asset.asset_id!r} != {asset_id!r}")
        self.preview_asset_path(asset)
        return asset

    def list_mask_assets(self) -> tuple[MaskAsset, ...]:
        return tuple(
            item
            for item in (
                self._load_list_entry(path.parent.name, self.load_mask_asset, path)
                for path in sorted((self._root / "masks").glob(f"*/{_MANIFEST_FILENAME}"))
            )
            if item is not None
        )

    def list_preview_assets(self) -> tuple[AssetRef, ...]:
        return tuple(
            item
            for item in (
                self._load_list_entry(path.parent.name, self.load_preview_asset, path)
                for path in sorted((self._root / "previews").glob(f"*/{_MANIFEST_FILENAME}"))
            )
            if item is not None
        )

    def save_source_snapshot(
        self,
        snapshot_png: bytes,
        *,
        snapshot_id: str,
        kind: str,
        width: int,
        height: int,
        dpi: int | None = None,
        document_id: str | None = None,
        page_id: str | None = None,
        layer_id: str | None = None,
        content_hash: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SourceSnapshotRef:
        snapshot_id = _validate_id("snapshot_id", snapshot_id)
        _validate_dimensions(width, height)
        snapshot_kind = str(kind).strip()
        if not snapshot_kind:
            raise ValueError("SAM3 source snapshot kind is required")
        snapshot_root = self._snapshot_root(snapshot_id)
        artifact_path = self._guard_path(snapshot_root / "source.png")
        artifact_sha = _sha256_bytes(snapshot_png)
        relative_path = self._relative_path(artifact_path)
        source_ref = SourceSnapshotRef(
            snapshot_id=snapshot_id,
            kind=snapshot_kind,
            width=int(width),
            height=int(height),
            dpi=dpi,
            document_id=document_id,
            page_id=page_id,
            layer_id=layer_id,
            content_hash=content_hash or artifact_sha,
            path=relative_path,
        )

        _atomic_write_bytes(artifact_path, snapshot_png)
        self._write_source_snapshot_manifest(
            snapshot_root / _MANIFEST_FILENAME,
            source_ref,
            len(snapshot_png),
            artifact_sha,
            metadata=metadata,
        )
        return source_ref

    def load_source_snapshot(self, snapshot_id: str) -> SourceSnapshotRef:
        manifest = _read_json_object(self._snapshot_manifest_path(snapshot_id))
        _require_schema(manifest, SAM3_SOURCE_SNAPSHOT_SCHEMA_VERSION)
        source_ref = SourceSnapshotRef.from_dict(_dict_field(manifest, "source_ref"))
        _validate_id("snapshot_id", source_ref.snapshot_id)
        if source_ref.snapshot_id != snapshot_id:
            raise ValueError(
                f"SAM3 source snapshot id mismatch: {source_ref.snapshot_id!r} != {snapshot_id!r}"
            )
        self.source_snapshot_path(source_ref)
        return source_ref

    def mask_asset_path(self, asset: MaskAsset | str) -> Path:
        if isinstance(asset, MaskAsset):
            return self._guard_relative_path(asset.relative_path)
        asset_id = _validate_id("asset_id", asset)
        return self._guard_path(self._mask_root(asset_id) / "mask.png")

    def preview_asset_path(self, asset: AssetRef | str) -> Path:
        if isinstance(asset, AssetRef):
            return self._guard_relative_path(asset.uri)
        asset_id = _validate_id("asset_id", asset)
        return self._guard_path(self._preview_root(asset_id) / "preview.png")

    def source_snapshot_path(self, ref: SourceSnapshotRef | str) -> Path:
        if isinstance(ref, SourceSnapshotRef):
            if ref.path is None:
                raise ValueError(f"SAM3 source snapshot path is missing: {ref.snapshot_id!r}")
            return self._guard_relative_path(ref.path)
        snapshot_id = _validate_id("snapshot_id", ref)
        return self._guard_path(self._snapshot_root(snapshot_id) / "source.png")

    def save_candidate_set(self, candidate_set: MaskCandidateSet) -> Path:
        set_id = _validate_id("candidate_set_id", candidate_set.set_id)
        path = self._candidate_set_path(set_id)
        _atomic_write_json(
            path,
            {
                "schema_version": SAM3_CANDIDATE_SET_SCHEMA_VERSION,
                "candidate_set": candidate_set.to_dict(),
            },
        )
        return path

    def load_candidate_set(self, set_id: str) -> MaskCandidateSet:
        set_id = _validate_id("candidate_set_id", set_id)
        payload = _read_json_object(self._candidate_set_path(set_id))
        _require_schema(payload, SAM3_CANDIDATE_SET_SCHEMA_VERSION)
        candidate_set = MaskCandidateSet.from_dict(_dict_field(payload, "candidate_set"))
        if candidate_set.set_id != set_id:
            raise ValueError(
                f"SAM3 candidate set id mismatch: {candidate_set.set_id!r} != {set_id!r}"
            )
        return candidate_set

    def list_candidate_sets(self) -> tuple[MaskCandidateSet, ...]:
        return tuple(
            item
            for item in (
                self._load_list_entry(path.stem, self.load_candidate_set, path)
                for path in sorted((self._root / "candidate_sets").glob("*.json"))
            )
            if item is not None
        )

    def save_job_status(self, status: Sam3JobStatus) -> Path:
        job_id = _validate_id("job_id", status.job_id)
        path = self._job_path(job_id)
        _atomic_write_json(
            path,
            {
                "schema_version": SAM3_JOB_SCHEMA_VERSION,
                "job": status.to_dict(),
            },
        )
        return path

    def load_job_status(self, job_id: str) -> Sam3JobStatus:
        job_id = _validate_id("job_id", job_id)
        payload = _read_json_object(self._job_path(job_id))
        _require_schema(payload, SAM3_JOB_SCHEMA_VERSION)
        status = Sam3JobStatus.from_dict(_dict_field(payload, "job"))
        if status.job_id != job_id:
            raise ValueError(f"SAM3 job id mismatch: {status.job_id!r} != {job_id!r}")
        return status

    def list_job_statuses(self) -> tuple[Sam3JobStatus, ...]:
        return tuple(
            item
            for item in (
                self._load_list_entry(path.stem, self.load_job_status, path)
                for path in sorted((self._root / "jobs").glob("*.json"))
            )
            if item is not None
        )

    def list_source_snapshots(self) -> tuple[SourceSnapshotRef, ...]:
        return tuple(
            item
            for item in (
                self._load_list_entry(path.parent.name, self.load_source_snapshot, path)
                for path in sorted((self._root / "snapshots").glob(f"*/{_MANIFEST_FILENAME}"))
            )
            if item is not None
        )

    def _write_asset_manifest(
        self,
        path: Path,
        asset: AssetRef | MaskAsset,
        size_bytes: int,
        artifact_sha: str,
    ) -> None:
        relative_path = asset.uri if isinstance(asset, AssetRef) else asset.relative_path
        _atomic_write_json(
            self._guard_path(path),
            {
                "schema_version": SAM3_ASSET_SCHEMA_VERSION,
                "asset": asset.to_dict(),
                "artifact": {
                    "relative_path": relative_path,
                    "size_bytes": int(size_bytes),
                    "sha256": artifact_sha,
                },
            },
        )

    def _write_source_snapshot_manifest(
        self,
        path: Path,
        source_ref: SourceSnapshotRef,
        size_bytes: int,
        artifact_sha: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        relative_path = source_ref.path
        _atomic_write_json(
            self._guard_path(path),
            {
                "schema_version": SAM3_SOURCE_SNAPSHOT_SCHEMA_VERSION,
                "source_ref": source_ref.to_dict(),
                "artifact": {
                    "relative_path": relative_path,
                    "size_bytes": int(size_bytes),
                    "sha256": artifact_sha,
                },
                "metadata": scrub_sam3_metadata(dict(metadata or {})),
            },
        )

    def _read_asset_manifest(self, path: Path) -> dict[str, Any]:
        payload = _read_json_object(path)
        _require_schema(payload, SAM3_ASSET_SCHEMA_VERSION)
        return payload

    def _mask_root(self, asset_id: str) -> Path:
        return self._guard_path(self._root / "masks" / _validate_id("asset_id", asset_id))

    def _preview_root(self, asset_id: str) -> Path:
        return self._guard_path(self._root / "previews" / _validate_id("asset_id", asset_id))

    def _snapshot_root(self, snapshot_id: str) -> Path:
        return self._guard_path(self._root / "snapshots" / _validate_id("snapshot_id", snapshot_id))

    def _mask_manifest_path(self, asset_id: str) -> Path:
        return self._guard_path(self._mask_root(asset_id) / _MANIFEST_FILENAME)

    def _preview_manifest_path(self, asset_id: str) -> Path:
        return self._guard_path(self._preview_root(asset_id) / _MANIFEST_FILENAME)

    def _snapshot_manifest_path(self, snapshot_id: str) -> Path:
        return self._guard_path(self._snapshot_root(snapshot_id) / _MANIFEST_FILENAME)

    def _candidate_set_path(self, set_id: str) -> Path:
        return self._guard_path(
            self._root / "candidate_sets" / f"{_validate_id('candidate_set_id', set_id)}.json"
        )

    def _job_path(self, job_id: str) -> Path:
        return self._guard_path(self._root / "jobs" / f"{_validate_id('job_id', job_id)}.json")

    def _guard_relative_path(self, relative_path: str) -> Path:
        text = str(relative_path)
        if not text or "\\" in text:
            raise ValueError(f"SAM3 asset relative path is not POSIX-style: {text!r}")
        posix_path = PurePosixPath(text)
        if posix_path.is_absolute():
            raise ValueError(f"SAM3 asset path must be relative: {text!r}")
        if any(part in {"", ".", ".."} or ":" in part for part in posix_path.parts):
            raise ValueError(f"SAM3 asset relative path is unsafe: {text!r}")
        return self._guard_path(self._root.joinpath(*posix_path.parts))

    def _relative_path(self, path: Path) -> str:
        return self._guard_path(path).relative_to(self._root).as_posix()

    def _guard_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != self._root and self._root not in resolved.parents:
            raise ValueError(f"SAM3 asset path escapes store root: {resolved}")
        return resolved

    def _load_list_entry(self, item_id: str, loader: Any, path: Path) -> Any | None:
        try:
            return loader(item_id)
        except (OSError, ValueError):
            logger.warning("Skipping invalid SAM3 asset metadata: %s", path)
            return None


def _default_asset_root() -> Path:
    from loomstroke.platform.user_dirs import default_user_root  # noqa: PLC0415

    return default_user_root(create=True) / "generated_assets" / "sam3"


def _validate_id(kind: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid SAM3 {kind}: {value!r}")
    if not value or value in {".", ".."} or not _ID_PATTERN.fullmatch(value):
        raise ValueError(f"invalid SAM3 {kind}: {value!r}")
    return value


def _validate_dimensions(width: int, height: int) -> None:
    if int(width) <= 0 or int(height) <= 0:
        raise ValueError(f"invalid SAM3 source snapshot dimensions: {width!r}x{height!r}")


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected JSON object")
    return raw


def _require_schema(payload: Mapping[str, Any], expected: str) -> None:
    if payload.get("schema_version") != expected:
        raise ValueError(f"unexpected SAM3 schema version: {payload.get('schema_version')!r}")


def _dict_field(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"SAM3 payload field {key!r} must be an object")
    return value


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    def _write(handle: Any) -> None:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)

    _atomic_write_text(path, _write)


def _atomic_write_text(path: Path, writer: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path_text = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=str(path.parent))
    tmp_path = Path(tmp_path_text)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path_text = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=str(path.parent))
    tmp_path = Path(tmp_path_text)
    try:
        with os.fdopen(tmp_fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "SAM3_ASSET_SCHEMA_VERSION",
    "SAM3_CANDIDATE_SET_SCHEMA_VERSION",
    "SAM3_JOB_SCHEMA_VERSION",
    "SAM3_SOURCE_SNAPSHOT_SCHEMA_VERSION",
    "Sam3AssetStore",
]
