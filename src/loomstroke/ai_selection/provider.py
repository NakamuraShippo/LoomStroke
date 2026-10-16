"""SAM3 mask provider facade for Tier 0 candidate/job persistence."""

from __future__ import annotations

import hashlib
import json
import re

from .assets import Sam3AssetStore
from .data_model import (
    AssetRef,
    BlockedReason,
    JobKind,
    MaskCandidateSet,
    OverlayOptions,
    Sam3JobStatus,
    Sam3Readiness,
    Sam3RefineRequest,
    Sam3SegmentRequest,
)
from .mask_asset_materializer import materialize_fake_mask_assets
from .overlay_preview import compose_overlay_preview_png
from .worker_client import (
    AiSelectionWorkerClient,
    AiSelectionWorkerUnavailable,
    UnavailableAiSelectionWorkerClient,
)

_SAFE_ID_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


class Sam3ProviderError(RuntimeError):
    """Base error for SAM3 provider facade failures."""


class Sam3OverlayPreviewUnavailable(Sam3ProviderError):
    """Raised when overlay preview generation is requested before it exists."""


class Sam3MaskProvider:
    """Compose a SAM3 worker client with the credential-free asset store."""

    def __init__(
        self,
        *,
        worker_client: AiSelectionWorkerClient | None = None,
        asset_store: Sam3AssetStore | None = None,
    ) -> None:
        self._worker_client = worker_client or UnavailableAiSelectionWorkerClient()
        self._asset_store = asset_store or Sam3AssetStore()

    @property
    def asset_store(self) -> Sam3AssetStore:
        return self._asset_store

    async def get_readiness(self) -> Sam3Readiness:
        return await self._worker_client.get_readiness()

    async def segment(self, request: Sam3SegmentRequest) -> MaskCandidateSet:
        try:
            candidate_set = await self._worker_client.segment(request)
        except AiSelectionWorkerUnavailable as exc:
            self._save_blocked_status(
                request_id=request.request_id,
                kind="segment_image",
                operation="segment",
                readiness=exc.readiness,
            )
            raise

        self._save_success(candidate_set, kind="segment_image")
        return candidate_set

    async def refine(self, request: Sam3RefineRequest) -> MaskCandidateSet:
        try:
            candidate_set = await self._worker_client.refine(request)
        except AiSelectionWorkerUnavailable as exc:
            self._save_blocked_status(
                request_id=request.request_id,
                kind="refine_mask",
                operation="refine",
                readiness=exc.readiness,
            )
            raise

        self._save_success(candidate_set, kind="refine_mask")
        return candidate_set

    async def create_overlay_preview(
        self,
        candidate_set_id: str,
        options: OverlayOptions,
    ) -> AssetRef:
        candidate_set = self._asset_store.load_candidate_set(candidate_set_id)
        option_payload = _normalized_overlay_options(options)
        preview_png, width, height, bbox, metadata = compose_overlay_preview_png(
            self._asset_store,
            candidate_set,
            options,
        )
        asset_id = _preview_asset_id(candidate_set_id, option_payload)
        return self._asset_store.save_preview_asset(
            preview_png,
            asset_id=asset_id,
            width=width,
            height=height,
            bbox=bbox,
            metadata={
                **metadata,
                "optionsDigest": _options_digest(candidate_set_id, option_payload),
            },
        )

    async def cancel(self, job_id: str) -> bool:
        cancelled = await self._worker_client.cancel(job_id)
        if cancelled:
            previous = self._load_existing_status(job_id)
            self._asset_store.save_job_status(
                Sam3JobStatus(
                    job_id=job_id,
                    kind=previous.kind if previous is not None else "segment_image",
                    status="cancelled",
                    terminal=True,
                    candidate_set_id=(previous.candidate_set_id if previous is not None else None),
                    output_asset_ids=(previous.output_asset_ids if previous is not None else ()),
                    diagnostics={
                        "provider": "sam3_mask_provider",
                        "source": "sam3_provider_facade",
                    },
                )
            )
        return cancelled

    async def close(self) -> None:
        await self._worker_client.close()

    def _load_existing_status(self, job_id: str) -> Sam3JobStatus | None:
        try:
            return self._asset_store.load_job_status(job_id)
        except (OSError, ValueError):
            return None

    def _save_success(self, candidate_set: MaskCandidateSet, *, kind: JobKind) -> None:
        materialized_asset_ids = materialize_fake_mask_assets(self._asset_store, candidate_set)
        self._asset_store.save_candidate_set(candidate_set)
        self._asset_store.save_job_status(
            Sam3JobStatus(
                job_id=candidate_set.job_id,
                kind=kind,
                status="completed",
                progress=1.0,
                terminal=True,
                candidate_set_id=candidate_set.set_id,
                output_asset_ids=_output_asset_ids(candidate_set),
                diagnostics={
                    "provider": "sam3_mask_provider",
                    "source": "sam3_provider_facade",
                    "candidateCount": len(candidate_set.candidates),
                    "materializedMaskAssetCount": len(materialized_asset_ids),
                },
            )
        )

    def _save_blocked_status(
        self,
        *,
        request_id: str,
        kind: JobKind,
        operation: str,
        readiness: Sam3Readiness | None,
    ) -> None:
        blocked_reasons = (
            readiness.blocked_reasons
            if readiness is not None
            else (
                BlockedReason(
                    code="worker_client_unavailable",
                    message="SAM3 worker client is unavailable.",
                    action="Configure a SAM3 worker before running segmentation.",
                ),
            )
        )
        recommended_actions = (
            readiness.recommended_actions
            if readiness is not None
            else tuple(reason.action for reason in blocked_reasons if reason.action)
        )
        self._asset_store.save_job_status(
            Sam3JobStatus(
                job_id=_provider_job_id(operation, request_id),
                kind=kind,
                status="blocked",
                terminal=True,
                blocked_reasons=tuple(blocked_reasons),
                recommended_actions=tuple(recommended_actions),
                diagnostics={
                    "provider": "sam3_mask_provider",
                    "source": "sam3_provider_facade",
                    "operation": operation,
                },
            )
        )


def _output_asset_ids(candidate_set: MaskCandidateSet) -> tuple[str, ...]:
    ids: list[str] = []
    seen: set[str] = set()
    for candidate in candidate_set.candidates:
        if candidate.mask_asset_id in seen:
            continue
        ids.append(candidate.mask_asset_id)
        seen.add(candidate.mask_asset_id)
    return tuple(ids)


def _provider_job_id(operation: str, request_id: str) -> str:
    return f"sam3-provider-{operation}-{_safe_id(request_id, fallback=operation)}"


def _preview_asset_id(candidate_set_id: str, option_payload: dict[str, object]) -> str:
    safe_set_id = _safe_id(candidate_set_id, fallback="candidate-set")
    return f"sam3-overlay-preview-{safe_set_id}-{_options_digest(candidate_set_id, option_payload)}"


def _normalized_overlay_options(options: OverlayOptions) -> dict[str, object]:
    payload = options.to_dict()
    color_mode = str(payload.get("color_mode") or "instance").strip().lower()
    if color_mode not in {"instance", "single", "heat"}:
        color_mode = "instance"
    return {
        "opacity": payload["opacity"],
        "color_mode": color_mode,
        "include_rejected": bool(payload.get("include_rejected", False)),
    }


def _options_digest(candidate_set_id: str, option_payload: dict[str, object]) -> str:
    payload = json.dumps(
        {"candidateSetId": candidate_set_id, "options": option_payload},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _safe_id(value: str, *, fallback: str) -> str:
    normalized = _SAFE_ID_PATTERN.sub("-", str(value).strip()).strip(".-_")
    if not normalized:
        normalized = fallback
    return normalized[:64]


__all__ = [
    "Sam3MaskProvider",
    "Sam3OverlayPreviewUnavailable",
    "Sam3ProviderError",
]
