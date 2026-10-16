"""Resumable checkpoint over the CT-22 pipeline (§5): steps, confirmations, retryable failures.

Generation success and canvas apply are separate steps; cancel never discards
completed steps, and a pending confirmation blocks the step until confirmed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

__all__ = ["ComicProductionCheckpoint"]

PIPELINE: tuple[str, ...] = (
    "script_import",
    "book_plan",
    "plan_validation",
    "bible_injection",
    "generation_plan",
    "generate_candidates",
    "qa_review",
    "candidate_selection",
    "apply_preview",
    "page_apply",
    "typesetting_preflight",
    "export_package",
)


@dataclass(frozen=True, slots=True)
class ComicProductionCheckpoint:
    job_id: str
    steps: tuple[str, ...] = PIPELINE
    completed: tuple[str, ...] = ()
    pending_confirmations: tuple[str, ...] = ()
    retryable_failures: tuple[str, ...] = ()
    failed_step: str | None = None
    cancelled: bool = False
    notes: Mapping[str, Any] = field(default_factory=dict)

    SCHEMA_VERSION: ClassVar[str] = "comic.production_checkpoint.v1"

    # -- queries -----------------------------------------------------------------------

    @property
    def current_step(self) -> str | None:
        for step in self.steps:
            if step not in self.completed:
                return step
        return None

    @property
    def is_complete(self) -> bool:
        return self.current_step is None and not self.cancelled

    @property
    def is_blocked(self) -> bool:
        return bool(self.pending_confirmations) or self.failed_step is not None

    def can_run(self, step: str) -> bool:
        return (
            not self.cancelled
            and step == self.current_step
            and not self.pending_confirmations
            and self.failed_step is None
        )

    def resume_point(self) -> str | None:
        """Step to continue from after a restart: the failed step, else the next one."""
        if self.cancelled:
            return None
        return self.failed_step or self.current_step

    # -- transitions -------------------------------------------------------------------

    def advance(self, step: str) -> ComicProductionCheckpoint:
        if not self.can_run(step):
            raise ValueError(f"checkpoint_step_not_runnable:{step}")
        return replace(self, completed=(*self.completed, step), failed_step=None)

    def require_confirmation(self, confirmation: str) -> ComicProductionCheckpoint:
        if confirmation in self.pending_confirmations:
            return self
        return replace(self, pending_confirmations=(*self.pending_confirmations, confirmation))

    def confirm(self, confirmation: str) -> ComicProductionCheckpoint:
        pending = tuple(c for c in self.pending_confirmations if c != confirmation)
        return replace(self, pending_confirmations=pending)

    def fail(self, step: str, *, retryable: bool) -> ComicProductionCheckpoint:
        failures = self.retryable_failures
        if retryable and step not in failures:
            failures = (*failures, step)
        return replace(self, failed_step=step, retryable_failures=failures)

    def retry(self) -> ComicProductionCheckpoint:
        if self.failed_step is None:
            return self
        if self.failed_step not in self.retryable_failures:
            raise ValueError(f"checkpoint_failure_not_retryable:{self.failed_step}")
        return replace(self, failed_step=None)

    def cancel(self) -> ComicProductionCheckpoint:
        return replace(self, cancelled=True)

    # -- (de)serialisation -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.SCHEMA_VERSION,
            "jobId": self.job_id,
            "steps": list(self.steps),
            "completed": list(self.completed),
            "currentStep": self.current_step,
            "pendingConfirmations": list(self.pending_confirmations),
            "retryableFailures": list(self.retryable_failures),
            "failedStep": self.failed_step,
            "cancelled": bool(self.cancelled),
            "notes": dict(self.notes),
        }

    @classmethod
    def from_dict(cls, data: Any) -> ComicProductionCheckpoint | None:
        if not isinstance(data, Mapping):
            return None
        job_id = data.get("jobId")
        if not isinstance(job_id, str) or not job_id:
            return None
        steps = tuple(str(s) for s in data.get("steps", PIPELINE) or PIPELINE)
        notes = data.get("notes")
        return cls(
            job_id=job_id,
            steps=steps,
            completed=tuple(str(s) for s in data.get("completed", []) if str(s) in steps),
            pending_confirmations=tuple(str(s) for s in data.get("pendingConfirmations", [])),
            retryable_failures=tuple(str(s) for s in data.get("retryableFailures", [])),
            failed_step=str(data["failedStep"]) if data.get("failedStep") else None,
            cancelled=bool(data.get("cancelled", False)),
            notes=dict(notes) if isinstance(notes, Mapping) else {},
        )
