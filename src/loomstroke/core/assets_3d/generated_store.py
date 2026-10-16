"""Generated 3D asset manifest and file store."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from loomstroke.platform.user_dirs import default_user_root

from .provider_contract import Generated3DProviderOutput, ProviderArtifact, mask_sensitive_metadata

MANIFEST_FILENAME = "manifest.json"


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """One artifact entry in a generated asset manifest."""

    kind: str
    role: str | None
    relative_path: str | None = None
    uri: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GeneratedAssetManifest:
    """Credential-free manifest for a generated 3D asset."""

    schema_version: str
    asset_id: str
    provider_id: str
    job_id: str
    contract_version: str
    created_utc: str
    artifacts: tuple[StoredArtifact, ...]
    prompt: str | None = None
    seed: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Generated3DAssetStore:
    """Store generated 3D artifacts under ``user_data/generated_assets/3d``."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or (default_user_root() / "generated_assets" / "3d")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def register_output(self, output: Generated3DProviderOutput) -> GeneratedAssetManifest:
        asset_id = uuid.uuid4().hex
        asset_root = self._guard_path(self.root / asset_id)
        artifacts_dir = self._guard_path(asset_root / "artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=False)

        stored: list[StoredArtifact] = []
        for index, artifact in enumerate(output.artifacts):
            stored.append(self._store_artifact(artifact, artifacts_dir, index=index))

        manifest = GeneratedAssetManifest(
            schema_version="loomstroke.generated_3d_asset.v1",
            asset_id=asset_id,
            provider_id=output.provider_id,
            job_id=output.job_id,
            contract_version=output.contract_version,
            created_utc=datetime.now(UTC).isoformat(timespec="seconds"),
            artifacts=tuple(stored),
            prompt=output.prompt,
            seed=output.seed,
            metadata=mask_sensitive_metadata(output.metadata),
        )
        self._write_manifest(asset_root / MANIFEST_FILENAME, manifest)
        return manifest

    def list_manifests(self) -> tuple[GeneratedAssetManifest, ...]:
        manifests: list[GeneratedAssetManifest] = []
        for manifest_path in sorted(self.root.glob(f"*/{MANIFEST_FILENAME}")):
            manifests.append(self.load_manifest(manifest_path.parent.name))
        return tuple(manifests)

    def load_manifest(self, asset_id: str) -> GeneratedAssetManifest:
        manifest_path = self._guard_asset_path(asset_id, MANIFEST_FILENAME)
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifacts = tuple(StoredArtifact(**entry) for entry in raw.get("artifacts", ()))
        return GeneratedAssetManifest(
            schema_version=raw["schema_version"],
            asset_id=raw["asset_id"],
            provider_id=raw["provider_id"],
            job_id=raw["job_id"],
            contract_version=raw["contract_version"],
            created_utc=raw["created_utc"],
            artifacts=artifacts,
            prompt=raw.get("prompt"),
            seed=raw.get("seed"),
            metadata=dict(raw.get("metadata") or {}),
        )

    def manifest_path(self, asset_id: str) -> Path:
        return self._guard_asset_path(asset_id, MANIFEST_FILENAME)

    def artifact_path(self, asset_id: str, relative_path: str) -> Path:
        return self._guard_asset_path(asset_id, relative_path)

    def _store_artifact(
        self,
        artifact: ProviderArtifact,
        artifacts_dir: Path,
        *,
        index: int,
    ) -> StoredArtifact:
        metadata = mask_sensitive_metadata(artifact.metadata)
        if artifact.local_path is not None:
            source = Path(artifact.local_path).expanduser().resolve()
            if not source.is_file():
                raise FileNotFoundError(source)
            suffix = source.suffix.lower()
            target = self._guard_path(artifacts_dir / f"asset-{index:03d}{suffix}")
            shutil.copy2(source, target)
            digest = self._sha256(target)
            return StoredArtifact(
                kind=artifact.kind,
                role=artifact.role,
                relative_path=target.relative_to(artifacts_dir.parent).as_posix(),
                mime_type=artifact.mime_type,
                size_bytes=target.stat().st_size,
                sha256=digest,
                metadata=metadata,
            )

        assert artifact.uri is not None
        return StoredArtifact(
            kind=artifact.kind,
            role=artifact.role,
            uri=self._safe_uri(artifact.uri),
            mime_type=artifact.mime_type,
            metadata=metadata,
        )

    def _write_manifest(self, path: Path, manifest: GeneratedAssetManifest) -> None:
        data = asdict(manifest)
        data["artifacts"] = [asdict(entry) for entry in manifest.artifacts]
        scrubbed = mask_sensitive_metadata(data)
        path.write_text(
            json.dumps(scrubbed, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _guard_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError(f"generated 3D asset path escapes store root: {resolved}")
        return resolved

    def _guard_asset_path(self, asset_id: str, relative_path: str) -> Path:
        asset_root = self._guard_path(self.root / asset_id)
        resolved = (asset_root / relative_path).resolve()
        if resolved != asset_root and asset_root not in resolved.parents:
            raise ValueError(f"generated 3D asset path escapes asset root: {resolved}")
        return resolved

    @staticmethod
    def _safe_uri(uri: str) -> str:
        parts = urlsplit(uri)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
