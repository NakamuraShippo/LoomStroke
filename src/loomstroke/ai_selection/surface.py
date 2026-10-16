"""Read-only SAM3 resource projection for later MCP/UI wiring."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote

from .assets import Sam3AssetStore
from .data_model import (
    AssetRef,
    MaskAsset,
    MaskCandidate,
    MaskCandidateSet,
    Sam3JobStatus,
    Sam3Readiness,
    SourceSnapshotRef,
)

SAM3_SURFACE_SCHEMA_VERSION = "sam3.surface.v1"
SAM3_READINESS_URI = "loomstroke://sam3/readiness"
_CANDIDATE_PREFIX = "loomstroke://sam3/candidate/"
_CANDIDATE_SET_PREFIX = "loomstroke://sam3/candidate-set/"
_JOB_PREFIX = "loomstroke://sam3/job/"
_MASK_ASSET_PREFIX = "loomstroke://sam3/mask-asset/"
_PREVIEW_ASSET_PREFIX = "loomstroke://sam3/preview-asset/"
_SOURCE_SNAPSHOT_PREFIX = "loomstroke://sam3/source-snapshot/"


class Sam3SurfaceResourceNotFound(KeyError):
    """Raised when a SAM3 read-only surface URI is not available."""

    def __init__(self, uri: str) -> None:
        self.uri = uri
        super().__init__(uri)


class Sam3ReadOnlySurface:
    """Read-only projection over SAM3 readiness, candidate sets, and jobs."""

    def __init__(
        self,
        asset_store: Sam3AssetStore,
        *,
        readiness: Sam3Readiness | None = None,
    ) -> None:
        self._asset_store = asset_store
        self._readiness = readiness
        self._readiness_revision = 0

    def begin_readiness_update(self) -> int:
        """Mark a new observation request and return its publication revision."""
        self._readiness_revision += 1
        return self._readiness_revision

    def update_readiness(self, readiness: Sam3Readiness, *, revision: int | None = None) -> None:
        """Publish a current observation without probing the provider.

        Direct updates supersede pending observations. An older async request
        cannot overwrite a newer request's projection when it finishes later.
        """
        if revision is None:
            self.begin_readiness_update()
        elif revision != self._readiness_revision:
            return
        self._readiness = readiness

    def list_resources(self) -> tuple[dict[str, Any], ...]:
        resources: list[dict[str, Any]] = []
        if self._readiness is not None:
            resources.append(
                _resource_descriptor(
                    SAM3_READINESS_URI,
                    name="SAM3 readiness",
                    kind="readiness",
                )
            )
        resources.extend(
            _resource_descriptor(
                _source_snapshot_uri(source_ref.snapshot_id),
                name=f"SAM3 source snapshot {source_ref.snapshot_id}",
                kind="source_snapshot",
            )
            for source_ref in self._asset_store.list_source_snapshots()
        )
        resources.extend(
            _resource_descriptor(
                _mask_asset_uri(mask_asset.asset_id),
                name=f"SAM3 mask asset {mask_asset.asset_id}",
                kind="mask_asset",
            )
            for mask_asset in self._asset_store.list_mask_assets()
        )
        resources.extend(
            _resource_descriptor(
                _preview_asset_uri(preview_asset.asset_id),
                name=f"SAM3 preview asset {preview_asset.asset_id}",
                kind="preview_asset",
            )
            for preview_asset in self._asset_store.list_preview_assets()
        )
        candidate_sets = self._asset_store.list_candidate_sets()
        resources.extend(
            _resource_descriptor(
                _candidate_set_uri(candidate_set.set_id),
                name=f"SAM3 candidate set {candidate_set.set_id}",
                kind="candidate_set",
            )
            for candidate_set in candidate_sets
        )
        seen_candidate_ids: set[str] = set()
        for candidate_set in candidate_sets:
            for candidate, _state in _candidate_entries(candidate_set):
                if candidate.candidate_id in seen_candidate_ids:
                    continue
                seen_candidate_ids.add(candidate.candidate_id)
                resources.append(
                    _resource_descriptor(
                        _candidate_uri(candidate.candidate_id),
                        name=f"SAM3 candidate {candidate.candidate_id}",
                        kind="candidate",
                    )
                )
        resources.extend(
            _resource_descriptor(
                _job_uri(status.job_id),
                name=f"SAM3 job {status.job_id}",
                kind="job_status",
            )
            for status in self._asset_store.list_job_statuses()
        )
        return tuple(resources)

    def read_resource(self, uri: str) -> dict[str, Any]:
        if uri == SAM3_READINESS_URI and self._readiness is not None:
            return sam3_readiness_resource(self._readiness)
        if uri.startswith(_SOURCE_SNAPSHOT_PREFIX):
            snapshot_id = unquote(uri.removeprefix(_SOURCE_SNAPSHOT_PREFIX))
            return sam3_source_snapshot_resource(
                self._asset_store.load_source_snapshot(snapshot_id)
            )
        asset_resource = self._read_asset_resource(uri)
        if asset_resource is not None:
            return asset_resource
        if uri.startswith(_CANDIDATE_SET_PREFIX):
            set_id = unquote(uri.removeprefix(_CANDIDATE_SET_PREFIX))
            return sam3_candidate_set_resource(self._asset_store.load_candidate_set(set_id))
        if uri.startswith(_CANDIDATE_PREFIX):
            candidate_id = unquote(uri.removeprefix(_CANDIDATE_PREFIX))
            for candidate_set in self._asset_store.list_candidate_sets():
                for candidate, state in _candidate_entries(candidate_set):
                    if candidate.candidate_id == candidate_id:
                        return sam3_candidate_resource(
                            candidate_set,
                            candidate,
                            candidate_state=state,
                        )
            raise Sam3SurfaceResourceNotFound(uri)
        if uri.startswith(_JOB_PREFIX):
            job_id = unquote(uri.removeprefix(_JOB_PREFIX))
            return sam3_job_status_resource(self._asset_store.load_job_status(job_id))
        raise Sam3SurfaceResourceNotFound(uri)

    def _read_asset_resource(self, uri: str) -> dict[str, Any] | None:
        if uri.startswith(_MASK_ASSET_PREFIX):
            asset_id = unquote(uri.removeprefix(_MASK_ASSET_PREFIX))
            return sam3_mask_asset_resource(self._asset_store.load_mask_asset(asset_id))
        if uri.startswith(_PREVIEW_ASSET_PREFIX):
            asset_id = unquote(uri.removeprefix(_PREVIEW_ASSET_PREFIX))
            return sam3_preview_asset_resource(self._asset_store.load_preview_asset(asset_id))
        return None


# Readiness diagnostics keys that are safe to expose over the MCP surface.
# Allowlist only: keys carrying absolute paths (assetStoreRoot,
# workerPythonPath, checkpointDir) or free-form process output (stderrTail)
# must never cross this boundary, and unknown future keys stay private by
# default. Local UI keeps the full diagnostics for the user's own machine.
_SAFE_READINESS_DIAGNOSTIC_KEYS = frozenset(
    {
        "allowRemoteWorker",
        "assetStoreError",
        "assetStoreWritable",
        "checkpointConfigured",
        "checkpointDirError",
        "checkpointDirExists",
        "configuredMode",
        "errorType",
        "externalServicesAllowed",
        "hfAccessConfigured",
        "hfAccessStatusError",
        "loomstrokePython",
        "pillowImportable",
        "provider",
        "sam3PackageImportable",
        "source",
        "torchImportable",
        "torchvisionImportable",
        "transformersImportable",
        "workerAutoDevice",
        "workerCudaAvailable",
        "workerDependencyError",
        "workerDependencyErrorType",
        "workerDependencyExitCode",
        "workerDependencyProbe",
        "workerDevice",
        "workerDeviceRequested",
        "workerErrorCode",
        "workerPython",
        "workerPythonError",
        "workerPythonExitCode",
        "workerPythonPathConfigured",
    }
)

_SAFE_READINESS_CAPABILITY_KEYS = frozenset(
    {
        "textPrompt",
        "pointPrompt",
        "boxPrompt",
        "boxExemplarPrompt",
        "maskPrompt",
        "assetExemplarPrompt",
        "negativeTextPrompt",
        "multipleBoxes",
        "combinedPointsAndBoxes",
        "pointRefine",
        "textRefine",
        "boxRefine",
        "cancelJob",
    }
)


def sam3_readiness_resource(readiness: Sam3Readiness) -> dict[str, Any]:
    payload = readiness.to_dict()
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, Mapping):
        payload["diagnostics"] = {
            key: value
            for key, value in diagnostics.items()
            if key in _SAFE_READINESS_DIAGNOSTIC_KEYS
        }
        capabilities = diagnostics.get("capabilities")
        if isinstance(capabilities, Mapping):
            payload["diagnostics"]["capabilities"] = {
                key: value
                for key, value in capabilities.items()
                if key in _SAFE_READINESS_CAPABILITY_KEYS and isinstance(value, bool)
            }
    return _resource_payload(
        SAM3_READINESS_URI,
        kind="readiness",
        payload_key="readiness",
        payload=payload,
    )


def sam3_candidate_set_resource(candidate_set: MaskCandidateSet) -> dict[str, Any]:
    return _resource_payload(
        _candidate_set_uri(candidate_set.set_id),
        kind="candidate_set",
        payload_key="candidateSet",
        payload=candidate_set.to_dict(),
    )


def sam3_candidate_resource(
    candidate_set: MaskCandidateSet,
    candidate: MaskCandidate,
    *,
    candidate_state: str = "active",
) -> dict[str, Any]:
    payload = _resource_payload(
        _candidate_uri(candidate.candidate_id),
        kind="candidate",
        payload_key="candidate",
        payload=candidate.to_dict(),
    )
    payload["candidateSetId"] = candidate_set.set_id
    payload["jobId"] = candidate_set.job_id
    payload["candidateState"] = candidate_state
    return payload


def sam3_mask_asset_resource(asset: MaskAsset) -> dict[str, Any]:
    return _resource_payload(
        _mask_asset_uri(asset.asset_id),
        kind="mask_asset",
        payload_key="maskAsset",
        payload=asset.to_dict(),
    )


def sam3_preview_asset_resource(asset: AssetRef) -> dict[str, Any]:
    return _resource_payload(
        _preview_asset_uri(asset.asset_id),
        kind="preview_asset",
        payload_key="previewAsset",
        payload=asset.to_dict(),
    )


def sam3_source_snapshot_resource(source_ref: SourceSnapshotRef) -> dict[str, Any]:
    return _resource_payload(
        _source_snapshot_uri(source_ref.snapshot_id),
        kind="source_snapshot",
        payload_key="sourceSnapshot",
        payload=source_ref.to_dict(),
    )


def sam3_job_status_resource(status: Sam3JobStatus) -> dict[str, Any]:
    return _resource_payload(
        _job_uri(status.job_id),
        kind="job_status",
        payload_key="jobStatus",
        payload=status.to_dict(),
    )


def _resource_descriptor(uri: str, *, name: str, kind: str) -> dict[str, Any]:
    return {
        "schemaVersion": SAM3_SURFACE_SCHEMA_VERSION,
        "uri": uri,
        "name": name,
        "kind": kind,
        "readOnly": True,
        "credentialValuesIncluded": False,
        "credentialReadPerformed": False,
    }


def _resource_payload(
    uri: str,
    *,
    kind: str,
    payload_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": SAM3_SURFACE_SCHEMA_VERSION,
        "uri": uri,
        "kind": kind,
        "credentialValuesIncluded": False,
        "credentialReadPerformed": False,
        payload_key: payload,
    }


def _candidate_entries(candidate_set: MaskCandidateSet) -> tuple[tuple[MaskCandidate, str], ...]:
    return (
        *((candidate, "active") for candidate in candidate_set.candidates),
        *((candidate, "rejected") for candidate in candidate_set.rejected_candidates),
    )


def _candidate_uri(candidate_id: str) -> str:
    return f"{_CANDIDATE_PREFIX}{candidate_id}"


def _candidate_set_uri(set_id: str) -> str:
    return f"{_CANDIDATE_SET_PREFIX}{set_id}"


def _job_uri(job_id: str) -> str:
    return f"{_JOB_PREFIX}{job_id}"


def _mask_asset_uri(asset_id: str) -> str:
    return f"{_MASK_ASSET_PREFIX}{asset_id}"


def _preview_asset_uri(asset_id: str) -> str:
    return f"{_PREVIEW_ASSET_PREFIX}{asset_id}"


def _source_snapshot_uri(snapshot_id: str) -> str:
    return f"{_SOURCE_SNAPSHOT_PREFIX}{snapshot_id}"


__all__ = [
    "SAM3_READINESS_URI",
    "SAM3_SURFACE_SCHEMA_VERSION",
    "Sam3ReadOnlySurface",
    "Sam3SurfaceResourceNotFound",
    "sam3_candidate_resource",
    "sam3_candidate_set_resource",
    "sam3_job_status_resource",
    "sam3_mask_asset_resource",
    "sam3_preview_asset_resource",
    "sam3_readiness_resource",
    "sam3_source_snapshot_resource",
]
