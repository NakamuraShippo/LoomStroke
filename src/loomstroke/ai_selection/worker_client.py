"""AI Select worker client contracts and safe in-process implementations.

The production backend is the SAM 3.1 worker (``loomstroke.sam31``); this
module holds the backend-neutral client protocol plus the unavailable/fake
clients used for fail-closed defaults and deterministic tests.
"""

from __future__ import annotations

import re
from typing import Protocol

from .data_model import (
    BlockedReason,
    MaskCandidate,
    MaskCandidateSet,
    Sam3Prompt,
    Sam3Readiness,
    Sam3RefineRequest,
    Sam3SegmentRequest,
    SourceSnapshotRef,
)

_SAFE_ID_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


class AiSelectionWorkerUnavailable(RuntimeError):
    """Raised when a SAM3 worker cannot perform inference."""

    def __init__(
        self,
        message: str = "SAM3 worker is unavailable.",
        *,
        readiness: Sam3Readiness | None = None,
    ) -> None:
        self.readiness = readiness
        super().__init__(message)


class AiSelectionWorkerClient(Protocol):
    async def get_readiness(self) -> Sam3Readiness: ...

    async def segment(self, request: Sam3SegmentRequest) -> MaskCandidateSet: ...

    async def refine(self, request: Sam3RefineRequest) -> MaskCandidateSet: ...

    async def cancel(self, job_id: str) -> bool: ...

    async def close(self) -> None: ...


class UnavailableAiSelectionWorkerClient:
    """Safe default worker client for environments without SAM3 configured."""

    def __init__(self, readiness: Sam3Readiness | None = None) -> None:
        self._readiness = readiness or _blocked_readiness(
            "worker_client_unavailable",
            "SAM3 worker client is unavailable.",
            "Configure a SAM3 worker before running segmentation.",
        )

    async def get_readiness(self) -> Sam3Readiness:
        return self._readiness

    async def segment(self, request: Sam3SegmentRequest) -> MaskCandidateSet:
        del request
        raise AiSelectionWorkerUnavailable(readiness=self._readiness)

    async def refine(self, request: Sam3RefineRequest) -> MaskCandidateSet:
        del request
        raise AiSelectionWorkerUnavailable(readiness=self._readiness)

    async def cancel(self, job_id: str) -> bool:
        del job_id
        return False

    async def close(self) -> None:
        return None


class FakeAiSelectionWorkerClient:
    """Deterministic fake SAM3 client for tests and provider facade development."""

    def __init__(
        self,
        *,
        readiness: Sam3Readiness | None = None,
        candidate_count: int = 1,
    ) -> None:
        self._readiness = readiness or Sam3Readiness(
            state="ready",
            diagnostics={"provider": "fake", "source": "sam3_fake_worker"},
        )
        self._candidate_count = max(1, int(candidate_count))
        self._known_job_ids: set[str] = set()
        self._closed = False

    async def get_readiness(self) -> Sam3Readiness:
        if self._closed:
            return _closed_readiness()
        return self._readiness

    async def segment(self, request: Sam3SegmentRequest) -> MaskCandidateSet:
        self._ensure_open()
        safe_id = _safe_id(request.request_id, fallback="segment")
        job_id = f"sam3-fake-job-{safe_id}"
        candidate_set = MaskCandidateSet(
            set_id=f"sam3-fake-set-{safe_id}",
            job_id=job_id,
            prompt=request.prompt,
            source_ref=request.source_ref,
            candidates=tuple(
                _fake_candidate(
                    request.source_ref,
                    request.prompt,
                    safe_id=safe_id,
                    index=index,
                    kind="segment",
                )
                for index in range(_candidate_count(request.max_candidates, self._candidate_count))
            ),
            diagnostics={
                "provider": "fake",
                "source": "sam3_fake_worker",
                "candidateCount": _candidate_count(request.max_candidates, self._candidate_count),
            },
        )
        self._known_job_ids.add(job_id)
        return candidate_set

    async def refine(self, request: Sam3RefineRequest) -> MaskCandidateSet:
        self._ensure_open()
        safe_id = _safe_id(request.request_id, fallback="refine")
        job_id = f"sam3-fake-refine-job-{safe_id}"
        candidate = _fake_candidate(
            request.source_ref,
            request.prompt,
            safe_id=safe_id,
            index=0,
            kind="refine",
            base_mask_asset_id=request.base_mask_asset_id,
        )
        candidate_set = MaskCandidateSet(
            set_id=f"sam3-fake-refine-set-{safe_id}",
            job_id=job_id,
            prompt=request.prompt,
            source_ref=request.source_ref,
            candidates=(candidate,),
            diagnostics={
                "provider": "fake",
                "source": "sam3_fake_worker",
                "candidateCount": 1,
            },
        )
        self._known_job_ids.add(job_id)
        return candidate_set

    async def cancel(self, job_id: str) -> bool:
        return job_id in self._known_job_ids

    async def close(self) -> None:
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise AiSelectionWorkerUnavailable(
                "SAM3 fake worker client is closed.",
                readiness=_closed_readiness(),
            )


def _fake_candidate(
    source_ref: SourceSnapshotRef,
    prompt: Sam3Prompt,
    *,
    safe_id: str,
    index: int,
    kind: str,
    base_mask_asset_id: str | None = None,
) -> MaskCandidate:
    bbox = _candidate_bbox(source_ref, index)
    area_px = bbox[2] * bbox[3]
    mask_prefix = "refined-mask" if kind == "refine" else "mask"
    preview_prefix = "refined-preview" if kind == "refine" else "preview"
    provenance = {
        "provider": "fake",
        "source": "sam3_fake_worker",
        "operation": kind,
        "requestSafeId": safe_id,
        "candidateIndex": index,
    }
    if base_mask_asset_id is not None:
        provenance["baseMaskAssetId"] = base_mask_asset_id
    return MaskCandidate(
        candidate_id=f"sam3-fake-{kind}-cand-{safe_id}-{index}",
        source_image_id=source_ref.snapshot_id,
        prompt=prompt,
        label=_candidate_label(prompt, kind=kind, index=index),
        score=0.99 if kind == "segment" else 0.97,
        bbox=bbox,
        area_px=area_px,
        mask_asset_id=f"sam3-fake-{mask_prefix}-{safe_id}-{index}",
        preview_asset_id=f"sam3-fake-{preview_prefix}-{safe_id}-{index}",
        instance_index=index,
        provenance=provenance,
    )


def _candidate_bbox(source_ref: SourceSnapshotRef, index: int) -> tuple[int, int, int, int]:
    source_width = max(1, int(source_ref.width))
    source_height = max(1, int(source_ref.height))
    width = max(1, source_width // 2)
    height = max(1, source_height // 2)
    x = min(index * max(1, width // 3), max(0, source_width - width))
    y = min(index * max(1, height // 3), max(0, source_height - height))
    return x, y, width, height


def _candidate_label(prompt: Sam3Prompt, *, kind: str, index: int) -> str:
    if isinstance(prompt.text, str) and prompt.text.strip():
        text = prompt.text
        base = text.strip()
    else:
        base = prompt.mode
    if kind == "refine":
        return f"refined_{base}"
    if index == 0:
        return base
    return f"{base}_{index}"


def _candidate_count(request_max: int, configured_count: int) -> int:
    return max(1, min(max(1, int(request_max)), configured_count))


def _safe_id(value: str, *, fallback: str) -> str:
    normalized = _SAFE_ID_PATTERN.sub("-", str(value).strip()).strip(".-_")
    if not normalized:
        normalized = fallback
    return normalized[:64]


def _suffixed_safe_id(base: str, suffix: str, *, fallback: str) -> str:
    """Compose ``base + suffix`` keeping the suffix inside the 64-char budget.

    ``_safe_id(f"{base}-{index}")`` silently truncates the tail when ``base``
    is long (the UI's default run request ids are 51 chars), collapsing every
    per-candidate id to the same value — the store then overwrites the same
    mask asset once per candidate and only the last mask survives
    (2026-07-13 field bug: refine always ran on the last candidate).
    """

    safe_suffix = _SAFE_ID_PATTERN.sub("-", str(suffix))
    keep = max(1, 64 - len(safe_suffix))
    safe_base = _safe_id(base, fallback=fallback)[:keep].rstrip(".-_")
    if not safe_base:
        safe_base = _safe_id(fallback, fallback="sam3")[:keep].rstrip(".-_") or "sam3"
    return f"{safe_base}{safe_suffix}"[:64]


def _blocked_readiness(
    code: str,
    message: str,
    action: str,
    *,
    runtime: str = "unavailable",
) -> Sam3Readiness:
    return Sam3Readiness(
        state="blocked",
        runtime=runtime,
        blocked_reasons=(BlockedReason(code=code, message=message, action=action),),
        recommended_actions=(action,),
        diagnostics={"provider": "unavailable", "source": "sam3_worker_client"},
        credential_values_included=False,
        credential_read_performed=False,
    )


def _closed_readiness() -> Sam3Readiness:
    return _blocked_readiness(
        "fake_worker_closed",
        "SAM3 fake worker client is closed.",
        "Create a new fake worker client before running segmentation.",
        runtime="fake",
    )


__all__ = [
    "AiSelectionWorkerClient",
    "AiSelectionWorkerUnavailable",
    "FakeAiSelectionWorkerClient",
    "UnavailableAiSelectionWorkerClient",
]
