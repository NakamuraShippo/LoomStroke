"""Pure data model for SAM3 Tier 0 integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .metadata_scrub import scrub_sam3_metadata

PromptMode = Literal["text", "point", "box", "mask", "exemplar", "mixed"]
PointLabel = Literal["positive", "negative"]
MaskAssetKind = Literal["alpha_mask", "binary_mask", "soft_mask", "instance_mask"]
ReadinessState = Literal["ready", "partial", "blocked", "unavailable"]
JobKind = Literal["segment_image", "segment_layer", "refine_mask", "track_video", "batch_recipe"]
JobState = Literal["queued", "running", "completed", "failed", "cancelled", "blocked"]
WarningSeverity = Literal["info", "warning", "error"]

SAM3_READINESS_SCHEMA_VERSION = "sam3.readiness.v1"
_JOB_KINDS = {
    "segment_image",
    "segment_layer",
    "refine_mask",
    "track_video",
    "batch_recipe",
}
_JOB_STATES = {"queued", "running", "completed", "failed", "cancelled", "blocked"}


@dataclass(frozen=True, slots=True)
class PointPrompt:
    x: float
    y: float
    label: PointLabel = "positive"

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "label": self.label}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PointPrompt:
        label = value.get("label", "positive")
        if label not in {"positive", "negative"}:
            label = "positive"
        return cls(x=float(value.get("x", 0.0)), y=float(value.get("y", 0.0)), label=label)


@dataclass(frozen=True, slots=True)
class BoxPrompt:
    x: float
    y: float
    width: float
    height: float
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BoxPrompt:
        label = value.get("label")
        return cls(
            x=float(value.get("x", 0.0)),
            y=float(value.get("y", 0.0)),
            width=float(value.get("width", 0.0)),
            height=float(value.get("height", 0.0)),
            label=str(label) if label is not None else None,
        )


@dataclass(frozen=True, slots=True)
class SourceSnapshotRef:
    snapshot_id: str
    kind: str
    width: int
    height: int
    dpi: int | None = None
    document_id: str | None = None
    page_id: str | None = None
    layer_id: str | None = None
    content_hash: str | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "kind": self.kind,
            "width": self.width,
            "height": self.height,
            "dpi": self.dpi,
            "document_id": self.document_id,
            "page_id": self.page_id,
            "layer_id": self.layer_id,
            "content_hash": self.content_hash,
            "path": self.path,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SourceSnapshotRef:
        return cls(
            snapshot_id=str(value.get("snapshot_id", "")),
            kind=str(value.get("kind", "unknown")),
            width=int(value.get("width", 0) or 0),
            height=int(value.get("height", 0) or 0),
            dpi=_optional_int(value.get("dpi")),
            document_id=_optional_str(value.get("document_id")),
            page_id=_optional_str(value.get("page_id")),
            layer_id=_optional_str(value.get("layer_id")),
            content_hash=_optional_str(value.get("content_hash")),
            path=_optional_str(value.get("path")),
        )


@dataclass(frozen=True, slots=True)
class Sam3Prompt:
    mode: PromptMode
    text: str | None = None
    points: tuple[PointPrompt, ...] = field(default_factory=tuple)
    boxes: tuple[BoxPrompt, ...] = field(default_factory=tuple)
    mask_asset_id: str | None = None
    exemplar_asset_ids: tuple[str, ...] = field(default_factory=tuple)
    negative_text: str | None = None
    locale: str | None = None
    normalized_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "text": self.text,
            "points": [point.to_dict() for point in self.points],
            "boxes": [box.to_dict() for box in self.boxes],
            "mask_asset_id": self.mask_asset_id,
            "exemplar_asset_ids": list(self.exemplar_asset_ids),
            "negative_text": self.negative_text,
            "locale": self.locale,
            "normalized_text": self.normalized_text,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Sam3Prompt:
        mode = value.get("mode", "text")
        if mode not in {"text", "point", "box", "mask", "exemplar", "mixed"}:
            mode = "text"
        return cls(
            mode=mode,
            text=_optional_str(value.get("text")),
            points=tuple(PointPrompt.from_dict(item) for item in _dict_items(value.get("points"))),
            boxes=tuple(BoxPrompt.from_dict(item) for item in _dict_items(value.get("boxes"))),
            mask_asset_id=_optional_str(value.get("mask_asset_id")),
            exemplar_asset_ids=tuple(
                str(item) for item in _list_items(value.get("exemplar_asset_ids"))
            ),
            negative_text=_optional_str(value.get("negative_text")),
            locale=_optional_str(value.get("locale")),
            normalized_text=_optional_str(value.get("normalized_text")),
        )


@dataclass(frozen=True, slots=True)
class AssetRef:
    asset_id: str
    uri: str
    kind: str
    width: int
    height: int
    content_hash: str
    bbox: tuple[int, int, int, int]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "uri": self.uri,
            "kind": self.kind,
            "width": self.width,
            "height": self.height,
            "content_hash": self.content_hash,
            "bbox": list(self.bbox),
            "metadata": scrub_sam3_metadata(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AssetRef:
        return cls(
            asset_id=str(value.get("asset_id", "")),
            uri=str(value.get("uri", "")),
            kind=str(value.get("kind", "")),
            width=int(value.get("width", 0) or 0),
            height=int(value.get("height", 0) or 0),
            content_hash=str(value.get("content_hash", "")),
            bbox=_int_tuple4(value.get("bbox")),
            metadata=_dict(value.get("metadata")),
        )


@dataclass(frozen=True, slots=True)
class MaskAsset:
    asset_id: str
    kind: MaskAssetKind
    width: int
    height: int
    relative_path: str
    content_hash: str
    bbox: tuple[int, int, int, int]
    source_candidate_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "kind": self.kind,
            "width": self.width,
            "height": self.height,
            "relative_path": self.relative_path,
            "content_hash": self.content_hash,
            "bbox": list(self.bbox),
            "source_candidate_id": self.source_candidate_id,
            "metadata": scrub_sam3_metadata(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MaskAsset:
        kind = value.get("kind", "alpha_mask")
        if kind not in {"alpha_mask", "binary_mask", "soft_mask", "instance_mask"}:
            kind = "alpha_mask"
        return cls(
            asset_id=str(value.get("asset_id", "")),
            kind=kind,
            width=int(value.get("width", 0) or 0),
            height=int(value.get("height", 0) or 0),
            relative_path=str(value.get("relative_path", "")),
            content_hash=str(value.get("content_hash", "")),
            bbox=_int_tuple4(value.get("bbox")),
            source_candidate_id=_optional_str(value.get("source_candidate_id")),
            metadata=_dict(value.get("metadata")),
        )


@dataclass(frozen=True, slots=True)
class Sam3Warning:
    code: str
    message: str
    severity: WarningSeverity = "warning"
    action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "action": self.action,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Sam3Warning:
        severity = value.get("severity", "warning")
        if severity not in {"info", "warning", "error"}:
            severity = "warning"
        return cls(
            code=str(value.get("code", "")),
            message=str(value.get("message", "")),
            severity=severity,
            action=_optional_str(value.get("action")),
        )


@dataclass(frozen=True, slots=True)
class BlockedReason:
    code: str
    message: str
    action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "action": self.action}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BlockedReason:
        return cls(
            code=str(value.get("code", "")),
            message=str(value.get("message", "")),
            action=_optional_str(value.get("action")),
        )


@dataclass(frozen=True, slots=True)
class MaskCandidate:
    candidate_id: str
    source_image_id: str
    prompt: Sam3Prompt
    label: str
    score: float | None
    bbox: tuple[int, int, int, int]
    area_px: int
    mask_asset_id: str
    preview_asset_id: str | None = None
    instance_index: int = 0
    group_id: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_image_id": self.source_image_id,
            "prompt": self.prompt.to_dict(),
            "label": self.label,
            "score": self.score,
            "bbox": list(self.bbox),
            "area_px": self.area_px,
            "mask_asset_id": self.mask_asset_id,
            "preview_asset_id": self.preview_asset_id,
            "instance_index": self.instance_index,
            "group_id": self.group_id,
            "provenance": scrub_sam3_metadata(self.provenance),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MaskCandidate:
        return cls(
            candidate_id=str(value.get("candidate_id", "")),
            source_image_id=str(value.get("source_image_id", "")),
            prompt=Sam3Prompt.from_dict(_dict(value.get("prompt"))),
            label=str(value.get("label", "")),
            score=_optional_float(value.get("score")),
            bbox=_int_tuple4(value.get("bbox")),
            area_px=int(value.get("area_px", 0) or 0),
            mask_asset_id=str(value.get("mask_asset_id", "")),
            preview_asset_id=_optional_str(value.get("preview_asset_id")),
            instance_index=int(value.get("instance_index", 0) or 0),
            group_id=_optional_str(value.get("group_id")),
            provenance=_dict(value.get("provenance")),
        )


@dataclass(frozen=True, slots=True)
class MaskCandidateSet:
    set_id: str
    job_id: str
    prompt: Sam3Prompt
    source_ref: SourceSnapshotRef
    candidates: tuple[MaskCandidate, ...] = field(default_factory=tuple)
    rejected_candidates: tuple[MaskCandidate, ...] = field(default_factory=tuple)
    warnings: tuple[Sam3Warning, ...] = field(default_factory=tuple)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "set_id": self.set_id,
            "job_id": self.job_id,
            "prompt": self.prompt.to_dict(),
            "source_ref": self.source_ref.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "rejected_candidates": [candidate.to_dict() for candidate in self.rejected_candidates],
            "warnings": [warning.to_dict() for warning in self.warnings],
            "diagnostics": scrub_sam3_metadata(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MaskCandidateSet:
        return cls(
            set_id=str(value.get("set_id", "")),
            job_id=str(value.get("job_id", "")),
            prompt=Sam3Prompt.from_dict(_dict(value.get("prompt"))),
            source_ref=SourceSnapshotRef.from_dict(_dict(value.get("source_ref"))),
            candidates=tuple(
                MaskCandidate.from_dict(item) for item in _dict_items(value.get("candidates"))
            ),
            rejected_candidates=tuple(
                MaskCandidate.from_dict(item)
                for item in _dict_items(value.get("rejected_candidates"))
            ),
            warnings=tuple(
                Sam3Warning.from_dict(item) for item in _dict_items(value.get("warnings"))
            ),
            diagnostics=_dict(value.get("diagnostics")),
        )


@dataclass(frozen=True, slots=True)
class Sam3Readiness:
    state: ReadinessState
    blocked_reasons: tuple[BlockedReason, ...] = field(default_factory=tuple)
    warnings: tuple[Sam3Warning, ...] = field(default_factory=tuple)
    recommended_actions: tuple[str, ...] = field(default_factory=tuple)
    runtime: str = "local_worker"
    diagnostics: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SAM3_READINESS_SCHEMA_VERSION
    credential_values_included: bool = False
    credential_read_performed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "state": self.state,
            "runtime": self.runtime,
            "credentialValuesIncluded": self.credential_values_included,
            "credentialReadPerformed": self.credential_read_performed,
            "blockedReasons": [reason.to_dict() for reason in self.blocked_reasons],
            "warnings": [warning.to_dict() for warning in self.warnings],
            "recommendedActions": list(self.recommended_actions),
            "diagnostics": scrub_sam3_metadata(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Sam3Readiness:
        state = value.get("state", "unavailable")
        if state not in {"ready", "partial", "blocked", "unavailable"}:
            state = "unavailable"
        return cls(
            state=state,
            blocked_reasons=tuple(
                BlockedReason.from_dict(item) for item in _dict_items(value.get("blockedReasons"))
            ),
            warnings=tuple(
                Sam3Warning.from_dict(item) for item in _dict_items(value.get("warnings"))
            ),
            recommended_actions=tuple(
                str(item) for item in _list_items(value.get("recommendedActions"))
            ),
            runtime=str(value.get("runtime", "local_worker")),
            diagnostics=_dict(value.get("diagnostics")),
            schema_version=str(value.get("schemaVersion", SAM3_READINESS_SCHEMA_VERSION)),
            credential_values_included=bool(value.get("credentialValuesIncluded", False)),
            credential_read_performed=bool(value.get("credentialReadPerformed", False)),
        )


@dataclass(frozen=True, slots=True)
class Sam3JobStatus:
    job_id: str
    kind: JobKind
    status: JobState
    progress: float | None = None
    terminal: bool = False
    timed_out: bool = False
    waited_ms: int | None = None
    candidate_set_id: str | None = None
    output_asset_ids: tuple[str, ...] = field(default_factory=tuple)
    blocked_reasons: tuple[BlockedReason, ...] = field(default_factory=tuple)
    recommended_actions: tuple[str, ...] = field(default_factory=tuple)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "jobId": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "terminal": self.terminal,
            "timedOut": self.timed_out,
            "waitedMs": self.waited_ms,
            "candidateSetId": self.candidate_set_id,
            "outputAssetIds": list(self.output_asset_ids),
            "blockedReasons": [reason.to_dict() for reason in self.blocked_reasons],
            "recommendedActions": list(self.recommended_actions),
            "diagnostics": scrub_sam3_metadata(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Sam3JobStatus:
        kind = value.get("kind", "segment_image")
        if kind not in _JOB_KINDS:
            kind = "segment_image"
        status = value.get("status", "blocked")
        if status not in _JOB_STATES:
            status = "blocked"
        return cls(
            job_id=str(value.get("jobId", "")),
            kind=kind,
            status=status,
            progress=_optional_float(value.get("progress")),
            terminal=bool(value.get("terminal", False)),
            timed_out=bool(value.get("timedOut", False)),
            waited_ms=_optional_int(value.get("waitedMs")),
            candidate_set_id=_optional_str(value.get("candidateSetId")),
            output_asset_ids=tuple(str(item) for item in _list_items(value.get("outputAssetIds"))),
            blocked_reasons=tuple(
                BlockedReason.from_dict(item) for item in _dict_items(value.get("blockedReasons"))
            ),
            recommended_actions=tuple(
                str(item) for item in _list_items(value.get("recommendedActions"))
            ),
            diagnostics=_dict(value.get("diagnostics")),
        )


@dataclass(frozen=True, slots=True)
class Sam3SegmentRequest:
    request_id: str
    source_ref: SourceSnapshotRef
    prompt: Sam3Prompt
    max_candidates: int = 16
    min_score: float = 0.2
    mask_threshold: float = 0.5
    mask_resolution_policy: str = "source"
    postprocess_policy: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    split_disconnected_instances: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "source_ref": self.source_ref.to_dict(),
            "prompt": self.prompt.to_dict(),
            "max_candidates": self.max_candidates,
            "min_score": self.min_score,
            "mask_threshold": self.mask_threshold,
            "mask_resolution_policy": self.mask_resolution_policy,
            "postprocess_policy": scrub_sam3_metadata(self.postprocess_policy),
            "metadata": scrub_sam3_metadata(self.metadata),
            "split_disconnected_instances": self.split_disconnected_instances,
        }


@dataclass(frozen=True, slots=True)
class Sam3RefineRequest:
    request_id: str
    source_ref: SourceSnapshotRef
    base_mask_asset_id: str
    prompt: Sam3Prompt
    options: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "source_ref": self.source_ref.to_dict(),
            "base_mask_asset_id": self.base_mask_asset_id,
            "prompt": self.prompt.to_dict(),
            "options": scrub_sam3_metadata(self.options),
            "metadata": scrub_sam3_metadata(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class OverlayOptions:
    opacity: float = 0.45
    color_mode: str = "instance"
    include_rejected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "opacity": max(0.0, min(1.0, float(self.opacity))),
            "color_mode": self.color_mode,
            "include_rejected": self.include_rejected,
        }


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _int_tuple4(value: Any) -> tuple[int, int, int, int]:
    items = list(_list_items(value))
    padded = [*items, 0, 0, 0, 0][:4]
    return tuple(int(item or 0) for item in padded)  # type: ignore[return-value]


def _dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return dict(scrub_sam3_metadata(value))


def _dict_items(value: Any) -> tuple[dict[str, Any], ...]:
    return tuple(item for item in _list_items(value) if isinstance(item, dict))


def _list_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


__all__ = [
    "SAM3_READINESS_SCHEMA_VERSION",
    "AssetRef",
    "BlockedReason",
    "BoxPrompt",
    "JobKind",
    "JobState",
    "MaskAsset",
    "MaskAssetKind",
    "MaskCandidate",
    "MaskCandidateSet",
    "OverlayOptions",
    "PointLabel",
    "PointPrompt",
    "PromptMode",
    "ReadinessState",
    "Sam3JobStatus",
    "Sam3Prompt",
    "Sam3Readiness",
    "Sam3RefineRequest",
    "Sam3SegmentRequest",
    "Sam3Warning",
    "SourceSnapshotRef",
    "WarningSeverity",
]
