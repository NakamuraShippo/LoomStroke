"""Pure SAM3 AI Select request and apply-plan helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from .data_model import (
    BlockedReason,
    BoxPrompt,
    MaskCandidate,
    MaskCandidateSet,
    PointPrompt,
    Sam3Prompt,
    Sam3SegmentRequest,
    Sam3Warning,
    SourceSnapshotRef,
)

SelectionApplyOperation = Literal["replace", "add", "subtract", "intersect"]
Sam3SelectionScope = Literal["active_layer", "visible_composite", "selection_bounds", "page"]
Sam3SelectionMode = Literal["prompt", "click", "box", "exemplar", "mixed"]
SelectionApplyStatus = Literal["ready", "blocked"]

SAM3_SELECTION_APPLY_PLAN_SCHEMA_VERSION = "sam3.selection_apply_plan.v1"

_APPLY_OPERATIONS = {"replace", "add", "subtract", "intersect"}
_SELECTION_SCOPES = {"active_layer", "visible_composite", "selection_bounds", "page"}
_SELECTION_MODES = {"prompt", "click", "box", "exemplar", "mixed"}


class Sam3SelectionBridgeError(ValueError):
    """Raised when an AI Select request cannot be built safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3SelectionContext:
    """Immutable source snapshot context for AI Select requests and apply checks."""

    snapshot_id: str
    scope: Sam3SelectionScope
    canvas_width: int
    canvas_height: int
    document_id: str | None = None
    page_id: str | None = None
    layer_id: str | None = None
    dpi: int | None = None
    content_hash: str | None = None
    snapshot_path: str | None = None
    available_layer_ids: tuple[str, ...] = field(default_factory=tuple)

    def to_source_ref(self) -> SourceSnapshotRef:
        _validate_dimensions(self.canvas_width, self.canvas_height)
        if self.scope not in _SELECTION_SCOPES:
            raise Sam3SelectionBridgeError(
                "invalid_selection_scope",
                f"Unsupported SAM3 selection scope: {self.scope!r}",
            )
        snapshot_id = str(self.snapshot_id).strip()
        if not snapshot_id:
            raise Sam3SelectionBridgeError(
                "snapshot_id_missing",
                "SAM3 selection context requires a snapshot id.",
            )
        return SourceSnapshotRef(
            snapshot_id=snapshot_id,
            kind=self.scope,
            width=int(self.canvas_width),
            height=int(self.canvas_height),
            dpi=self.dpi,
            document_id=self.document_id,
            page_id=self.page_id,
            layer_id=self.layer_id,
            content_hash=self.content_hash,
            path=self.snapshot_path,
        )


@dataclass(frozen=True, slots=True)
class Sam3SelectionPromptSpec:
    """UI-facing AI Select prompt specification."""

    mode: Sam3SelectionMode
    prompt_text: str | None = None
    points: tuple[PointPrompt, ...] = field(default_factory=tuple)
    boxes: tuple[BoxPrompt, ...] = field(default_factory=tuple)
    exemplar_asset_ids: tuple[str, ...] = field(default_factory=tuple)
    negative_text: str | None = None
    locale: str | None = None
    normalized_text: str | None = None

    def to_prompt(self) -> Sam3Prompt:
        if self.mode not in _SELECTION_MODES:
            raise Sam3SelectionBridgeError(
                "invalid_selection_mode",
                f"Unsupported SAM3 selection mode: {self.mode!r}",
            )

        text = _optional_clean_text(self.prompt_text)
        negative_text = _optional_clean_text(self.negative_text)
        normalized_text = _optional_clean_text(self.normalized_text)

        if self.mode == "prompt":
            if text is None:
                raise Sam3SelectionBridgeError(
                    "prompt_text_required",
                    "Prompt select requires prompt text.",
                )
            prompt_mode = "text"
        elif self.mode == "click":
            if not self.points:
                raise Sam3SelectionBridgeError(
                    "point_prompt_required",
                    "Click-to-mask requires at least one point prompt.",
                )
            prompt_mode = "point"
        elif self.mode == "box":
            if not self.boxes:
                raise Sam3SelectionBridgeError(
                    "box_prompt_required",
                    "Box-to-mask requires at least one box prompt.",
                )
            prompt_mode = "box"
        elif self.mode == "exemplar":
            if not self.exemplar_asset_ids:
                raise Sam3SelectionBridgeError(
                    "exemplar_asset_required",
                    "Exemplar select requires at least one exemplar asset id.",
                )
            prompt_mode = "exemplar"
        else:
            if text is None and not self.points and not self.boxes and not self.exemplar_asset_ids:
                raise Sam3SelectionBridgeError(
                    "mixed_prompt_required",
                    "Mixed SAM3 selection requires at least one prompt input.",
                )
            prompt_mode = "mixed"

        return Sam3Prompt(
            mode=prompt_mode,
            text=text,
            points=tuple(self.points),
            boxes=tuple(self.boxes),
            exemplar_asset_ids=tuple(str(item) for item in self.exemplar_asset_ids),
            negative_text=negative_text,
            locale=_optional_clean_text(self.locale),
            normalized_text=normalized_text,
        )


@dataclass(frozen=True, slots=True)
class Sam3SelectionApplyPlan:
    """Document-free plan for applying a SAM3 candidate as a raster selection."""

    candidate_set_id: str
    candidate_id: str
    mask_asset_id: str | None
    operation: SelectionApplyOperation
    source_ref: SourceSnapshotRef
    selection_bounds: tuple[int, int, int, int] | None
    status: SelectionApplyStatus
    blocked_reasons: tuple[BlockedReason, ...] = field(default_factory=tuple)
    warnings: tuple[Sam3Warning, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": SAM3_SELECTION_APPLY_PLAN_SCHEMA_VERSION,
            "candidateSetId": self.candidate_set_id,
            "candidateId": self.candidate_id,
            "maskAssetId": self.mask_asset_id,
            "operation": self.operation,
            "sourceRef": self.source_ref.to_dict(),
            "selectionBounds": (
                list(self.selection_bounds) if self.selection_bounds is not None else None
            ),
            "status": self.status,
            "ready": self.ready,
            "blockedReasons": [reason.to_dict() for reason in self.blocked_reasons],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


def normalize_selection_apply_operation(value: str) -> SelectionApplyOperation:
    raw = str(value).strip()
    if raw in _APPLY_OPERATIONS:
        return cast("SelectionApplyOperation", raw)
    raise Sam3SelectionBridgeError(
        "invalid_apply_operation",
        f"Unsupported SAM3 selection apply operation: {value!r}",
    )


def build_selection_segment_request(
    request_id: str,
    context: Sam3SelectionContext,
    prompt_spec: Sam3SelectionPromptSpec,
    *,
    max_candidates: int = 16,
    min_score: float = 0.2,
    mask_threshold: float = 0.5,
    split_disconnected_instances: bool = True,
    auto_mask_cleanup: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> Sam3SegmentRequest:
    request_id = str(request_id).strip()
    if not request_id:
        raise Sam3SelectionBridgeError(
            "request_id_missing",
            "SAM3 selection request requires a request id.",
        )

    request_metadata = dict(metadata or {})
    request_metadata["selectionScope"] = context.scope
    request_metadata["selectionMode"] = prompt_spec.mode

    # Default False keeps raw requests byte-identical to the worker output;
    # the AI Select tool opts in via its auto_mask_cleanup setting.
    postprocess_policy: dict[str, Any] = {}
    if auto_mask_cleanup:
        postprocess_policy["autoCleanup"] = True

    return Sam3SegmentRequest(
        request_id=request_id,
        source_ref=context.to_source_ref(),
        prompt=prompt_spec.to_prompt(),
        max_candidates=max(1, int(max_candidates)),
        min_score=_clamp_score(min_score),
        mask_threshold=_clamp_score(mask_threshold),
        split_disconnected_instances=bool(split_disconnected_instances),
        postprocess_policy=postprocess_policy,
        metadata=request_metadata,
    )


def build_selection_apply_plan(
    candidate_set: MaskCandidateSet,
    candidate_id: str,
    current_context: Sam3SelectionContext,
    *,
    operation: str,
) -> Sam3SelectionApplyPlan:
    blocked_reasons: list[BlockedReason] = []
    warnings: list[Sam3Warning] = []
    source_ref = candidate_set.source_ref
    normalized_operation: SelectionApplyOperation = "replace"

    try:
        normalized_operation = normalize_selection_apply_operation(operation)
    except Sam3SelectionBridgeError:
        blocked_reasons.append(
            _blocked_reason(
                "invalid_apply_operation",
                "SAM3 selection apply operation must be a raw operation value.",
                "Use replace, add, subtract, or intersect.",
            )
        )

    candidate = _find_candidate(candidate_set, candidate_id)
    if candidate is None:
        blocked_reasons.append(
            _blocked_reason(
                "candidate_missing",
                "SAM3 candidate is not available in the candidate set.",
                "Run AI Select again before applying the candidate.",
            )
        )

    _append_stale_context_reasons(source_ref, current_context, blocked_reasons, warnings)

    selection_bounds: tuple[int, int, int, int] | None = None
    mask_asset_id: str | None = None
    if candidate is not None:
        mask_asset_id = candidate.mask_asset_id or None
        if mask_asset_id is None:
            blocked_reasons.append(
                _blocked_reason(
                    "candidate_mask_missing",
                    "SAM3 candidate does not reference a mask asset.",
                    "Run AI Select again before applying the candidate.",
                )
            )

        selection_bounds, clipped = _clip_candidate_bounds(
            candidate,
            source_ref.width,
            source_ref.height,
        )
        if selection_bounds is None:
            blocked_reasons.append(
                _blocked_reason(
                    "candidate_bounds_invalid",
                    "SAM3 candidate bounds do not intersect the source canvas.",
                    "Run AI Select again before applying the candidate.",
                )
            )
        elif clipped:
            warnings.append(
                Sam3Warning(
                    code="candidate_bounds_clipped",
                    message="SAM3 candidate bounds were clipped to the source canvas.",
                    severity="warning",
                    action="Inspect the candidate preview before applying.",
                )
            )

    return Sam3SelectionApplyPlan(
        candidate_set_id=candidate_set.set_id,
        candidate_id=candidate_id,
        mask_asset_id=mask_asset_id,
        operation=normalized_operation,
        source_ref=source_ref,
        selection_bounds=selection_bounds,
        status="blocked" if blocked_reasons else "ready",
        blocked_reasons=tuple(blocked_reasons),
        warnings=tuple(warnings),
    )


def _append_stale_context_reasons(
    source_ref: SourceSnapshotRef,
    current_context: Sam3SelectionContext,
    blocked_reasons: list[BlockedReason],
    warnings: list[Sam3Warning],
) -> None:
    if (
        source_ref.document_id is not None
        and current_context.document_id is not None
        and source_ref.document_id != current_context.document_id
    ):
        blocked_reasons.append(
            _blocked_reason(
                "stale_document",
                "SAM3 candidate was created for a different document.",
                "Run AI Select again in the current document.",
            )
        )

    if (
        source_ref.page_id is not None
        and current_context.page_id is not None
        and source_ref.page_id != current_context.page_id
    ):
        blocked_reasons.append(
            _blocked_reason(
                "stale_page",
                "SAM3 candidate was created for a different page.",
                "Run AI Select again on the active page.",
            )
        )

    if int(source_ref.width) != int(current_context.canvas_width) or int(source_ref.height) != int(
        current_context.canvas_height
    ):
        blocked_reasons.append(
            _blocked_reason(
                "source_canvas_size_changed",
                "SAM3 candidate source canvas size no longer matches the current canvas.",
                "Run AI Select again after resizing or switching context.",
            )
        )

    if (
        source_ref.layer_id
        and current_context.available_layer_ids
        and source_ref.layer_id not in current_context.available_layer_ids
    ):
        if source_ref.kind == "visible_composite":
            warnings.append(
                Sam3Warning(
                    code="source_layer_missing_visible_composite",
                    message=(
                        "SAM3 candidate source layer is gone, but the candidate was created "
                        "from a visible composite snapshot."
                    ),
                    severity="warning",
                    action="Inspect the candidate preview before applying.",
                )
            )
        else:
            blocked_reasons.append(
                _blocked_reason(
                    "source_layer_missing",
                    "SAM3 candidate source layer is no longer available.",
                    "Run AI Select again for the current layer.",
                )
            )


def _find_candidate(candidate_set: MaskCandidateSet, candidate_id: str) -> MaskCandidate | None:
    for candidate in candidate_set.candidates:
        if candidate.candidate_id == candidate_id:
            return candidate
    return None


def _clip_candidate_bounds(
    candidate: MaskCandidate,
    source_width: int,
    source_height: int,
) -> tuple[tuple[int, int, int, int] | None, bool]:
    if source_width <= 0 or source_height <= 0:
        return None, False
    x, y, width, height = candidate.bbox
    if width <= 0 or height <= 0:
        return None, False

    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(int(source_width), int(x + width))
    y1 = min(int(source_height), int(y + height))
    if x1 <= x0 or y1 <= y0:
        return None, False

    clipped_bounds = (x0, y0, x1 - x0, y1 - y0)
    original_bounds = (int(x), int(y), int(width), int(height))
    return clipped_bounds, clipped_bounds != original_bounds


def _blocked_reason(code: str, message: str, action: str) -> BlockedReason:
    return BlockedReason(code=code, message=message, action=action)


def _optional_clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _validate_dimensions(width: int, height: int) -> None:
    if int(width) <= 0 or int(height) <= 0:
        raise Sam3SelectionBridgeError(
            "invalid_canvas_size",
            "SAM3 selection context requires a positive canvas size.",
        )


def _clamp_score(value: float) -> float:
    score = float(value)
    return max(0.0, min(1.0, score))


__all__ = [
    "SAM3_SELECTION_APPLY_PLAN_SCHEMA_VERSION",
    "Sam3SelectionApplyPlan",
    "Sam3SelectionBridgeError",
    "Sam3SelectionContext",
    "Sam3SelectionMode",
    "Sam3SelectionPromptSpec",
    "Sam3SelectionScope",
    "SelectionApplyOperation",
    "SelectionApplyStatus",
    "build_selection_apply_plan",
    "build_selection_segment_request",
    "normalize_selection_apply_operation",
]
