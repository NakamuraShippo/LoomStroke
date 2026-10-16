"""UI-independent SAM3 selection apply execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .assets import Sam3AssetStore
from .data_model import BlockedReason, Sam3Warning
from .mask_refine import MaskRefineOptions
from .selection_bridge import Sam3SelectionApplyPlan
from .selection_command_adapter import (
    Sam3SelectionCommandAdapterError,
    build_selection_apply_command,
)
from .selection_session import Sam3SelectionSession, build_session_apply_plan

SAM3_SELECTION_APPLY_EXECUTION_SCHEMA_VERSION = "sam3.selection_apply_execution.v1"
_DEFAULT_SOURCE = "sam3.selection.apply"
_ADAPTER_FAILURE_ACTION = "Run AI Select again before applying the candidate."
_SUBMIT_REJECTED_ACTION = "Inspect the current canvas state and retry the apply operation."

Sam3SelectionApplyExecutionStatus = Literal["applied", "blocked"]


class Sam3SelectionCanvasSubmitter(Protocol):
    """Canvas-like command boundary used by SAM3 apply execution."""

    def submit(self, cmd: object, *, merge_key: str | None = None) -> bool: ...


@dataclass(frozen=True, slots=True)
class Sam3SelectionApplyExecutionResult:
    """Result payload for applying one SAM3 candidate as a selection."""

    plan: Sam3SelectionApplyPlan
    status: Sam3SelectionApplyExecutionStatus
    submitted: bool = False
    command_source: str | None = None
    blocked_reasons: tuple[BlockedReason, ...] = field(default_factory=tuple)
    warnings: tuple[Sam3Warning, ...] = field(default_factory=tuple)
    schema_version: str = SAM3_SELECTION_APPLY_EXECUTION_SCHEMA_VERSION

    @property
    def applied(self) -> bool:
        return self.status == "applied"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "status": self.status,
            "applied": self.applied,
            "submitted": self.submitted,
            "commandSource": self.command_source,
            "plan": self.plan.to_dict(),
            "blockedReasons": [reason.to_dict() for reason in self.blocked_reasons],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


def apply_selection_session(
    session: Sam3SelectionSession,
    asset_store: Sam3AssetStore,
    canvas: Sam3SelectionCanvasSubmitter,
    *,
    source: str | None = _DEFAULT_SOURCE,
    merge_key: str | None = None,
    refine_options: MaskRefineOptions | None = None,
) -> Sam3SelectionApplyExecutionResult:
    """Apply the selected candidate from a SAM3 session through ``canvas.submit``."""

    return apply_selection_plan(
        build_session_apply_plan(session),
        asset_store,
        canvas,
        source=source,
        merge_key=merge_key,
        refine_options=refine_options,
    )


def apply_selection_plan(
    plan: Sam3SelectionApplyPlan,
    asset_store: Sam3AssetStore,
    canvas: Sam3SelectionCanvasSubmitter,
    *,
    source: str | None = _DEFAULT_SOURCE,
    merge_key: str | None = None,
    refine_options: MaskRefineOptions | None = None,
) -> Sam3SelectionApplyExecutionResult:
    """Apply a ready SAM3 selection plan through the canvas command dispatcher."""

    warnings = tuple(plan.warnings)
    if not plan.ready:
        return _blocked_result(
            plan,
            reasons=plan.blocked_reasons,
            warnings=warnings,
            command_source=source,
        )

    try:
        command = build_selection_apply_command(
            plan, asset_store, source=source, refine_options=refine_options
        )
    except Sam3SelectionCommandAdapterError as exc:
        return _blocked_result(
            plan,
            reasons=(_adapter_blocked_reason(exc),),
            warnings=warnings,
            command_source=source,
        )

    submitted = bool(canvas.submit(command, merge_key=merge_key))
    if not submitted:
        return _blocked_result(
            plan,
            reasons=(_submit_rejected_reason(),),
            warnings=warnings,
            command_source=source,
        )

    return Sam3SelectionApplyExecutionResult(
        plan=plan,
        status="applied",
        submitted=True,
        command_source=source,
        warnings=warnings,
    )


def _blocked_result(
    plan: Sam3SelectionApplyPlan,
    *,
    reasons: tuple[BlockedReason, ...],
    warnings: tuple[Sam3Warning, ...],
    command_source: str | None,
) -> Sam3SelectionApplyExecutionResult:
    return Sam3SelectionApplyExecutionResult(
        plan=plan,
        status="blocked",
        submitted=False,
        command_source=command_source,
        blocked_reasons=tuple(reasons),
        warnings=warnings,
    )


def _adapter_blocked_reason(exc: Sam3SelectionCommandAdapterError) -> BlockedReason:
    return BlockedReason(
        code=exc.code,
        message=str(exc),
        action=_ADAPTER_FAILURE_ACTION,
    )


def _submit_rejected_reason() -> BlockedReason:
    return BlockedReason(
        code="canvas_submit_rejected",
        message="The canvas rejected the SAM3 selection apply command.",
        action=_SUBMIT_REJECTED_ACTION,
    )


__all__ = [
    "SAM3_SELECTION_APPLY_EXECUTION_SCHEMA_VERSION",
    "Sam3SelectionApplyExecutionResult",
    "Sam3SelectionApplyExecutionStatus",
    "Sam3SelectionCanvasSubmitter",
    "apply_selection_plan",
    "apply_selection_session",
]
