"""Lifecycle state of one preview / inpaint submit (job id, status, cancelable) (CT-23 §7.3)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, ClassVar

__all__ = ["Sam3AsyncSubmitState"]


@dataclass(frozen=True, slots=True)
class Sam3AsyncSubmitState:
    action_id: str
    status: str = "queued"
    job_id: str | None = None
    effect_layer_id: str | None = None
    cancelable: bool = False
    blocked_reason_codes: tuple[str, ...] = ()

    TERMINAL: ClassVar[frozenset[str]] = frozenset({"succeeded", "failed", "cancelled", "blocked"})
    CANCELABLE: ClassVar[frozenset[str]] = frozenset({"queued", "generating"})

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL

    @classmethod
    def from_submit_result(
        cls, action_id: str, result: Mapping[str, Any], *, cancel_supported: bool
    ) -> Sam3AsyncSubmitState:
        status = str(result.get("status") or "")
        preview = result.get("previewSubmit")
        preview = preview if isinstance(preview, Mapping) else {}
        job = preview.get("job")
        job = job if isinstance(job, Mapping) else {}
        job_id = job.get("jobId") or job.get("id")
        layer_id = preview.get("effectLayerId")
        if status == "submitted":
            lifecycle = str(job.get("status") or "queued")
            if lifecycle not in {"queued", "generating"}:
                lifecycle = "queued"
        elif status == "blocked":
            lifecycle = "blocked"
        else:
            lifecycle = "failed"
        cancelable = cancel_supported and lifecycle in cls.CANCELABLE and bool(layer_id or job_id)
        codes = result.get("blockedReasonCodes")
        return cls(
            action_id=action_id,
            status=lifecycle,
            job_id=str(job_id) if job_id else None,
            effect_layer_id=str(layer_id) if layer_id else None,
            cancelable=cancelable,
            blocked_reason_codes=tuple(str(c) for c in codes) if isinstance(codes, list) else (),
        )

    def with_status(self, status: str, *, cancelable: bool | None = None) -> Sam3AsyncSubmitState:
        nxt = replace(self, status=status)
        if cancelable is None:
            cancelable = nxt.cancelable and status in self.CANCELABLE
        return replace(nxt, cancelable=cancelable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "actionId": self.action_id,
            "status": self.status,
            "jobId": self.job_id,
            "effectLayerId": self.effect_layer_id,
            "cancelable": bool(self.cancelable),
            "blockedReasonCodes": list(self.blocked_reason_codes),
        }
