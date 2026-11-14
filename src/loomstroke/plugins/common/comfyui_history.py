"""Pure ComfyUI history completion and private readiness/error signals."""

from __future__ import annotations

from inspect import getattr_static
from typing import Any

from loomstroke.core.events import JobProgressEvent
from loomstroke.plugins.ai_effect.errors import AIError, ErrorClass
from loomstroke.plugins.sdk.base import ProgressEvent

_STANDALONE_HISTORY_TIMEOUT_SEC = 30.0
_HISTORY_UNAVAILABLE_ERROR = "ComfyUI history never became available"
_MISSING_SELECTED_IMAGE_OUTPUT_ERROR = "ComfyUI completed without any of the selected image outputs"
_SELECTED_MODEL_OUTPUT_ERROR = "Generated outputs could not be fully applied"


class _PendingHistoryProgress(ProgressEvent):
    """An ended stream needs history polling; no public progress fields added."""


class _PendingHistoryEvent(JobProgressEvent):
    """Internal bus delivery of the same readiness hint, without a success result."""


class _HistoryTerminalError(RuntimeError):
    def __init__(self, kind: str, error: AIError | None) -> None:
        self.kind = kind
        self.error = error
        super().__init__(
            (error.detail or error.title) if error is not None else "ComfyUI job cancelled"
        )


def _raise_terminal_failure(job: Any) -> None:
    """Check the bound job's sanitized fatal receipt, without changing require()."""
    failure = getattr_static(job, "_terminal_failure", None) if job is not None else None
    if isinstance(failure, tuple):
        raise _HistoryTerminalError(*failure)


def _history_status_label(status: Any) -> str:
    if isinstance(status, str):
        return status.lower()
    if isinstance(status, dict):
        label = status.get("status_str") or status.get("status") or status.get("state") or ""
        return str(label).lower()
    return ""


def _history_has_explicit_completion(record: Any) -> bool:
    """Distinguish a classified success from a status-less legacy history."""
    if not isinstance(record, dict):
        return False
    status = record.get("status")
    return (
        record.get("completed") is True
        or (isinstance(status, dict) and status.get("completed") is True)
        or _history_status_label(status) in {"success", "completed"}
    )


def history_completion(history: Any, *, job_id: str) -> tuple[str, AIError | None]:
    """Classify completion, keeping status-less legacy records compatible.

    Images may precede a later node's failure. Never copy the prompt, current
    inputs or traceback into the error payload. Older panel aliases are read
    here too, so consumers do not maintain a second terminal classifier.
    """
    record = history if isinstance(history, dict) else {}
    if not any(key in record for key in ("outputs", "status", "prompt", "completed", "error")):
        record = record.get(job_id, {})
    record = record if isinstance(record, dict) else {}
    status = record.get("status")
    status_fields = status if isinstance(status, dict) else {}
    messages = status_fields.get("messages")
    if not isinstance(messages, list):
        messages = []
    for message in reversed(messages):
        if not isinstance(message, (list, tuple)) or len(message) != 2:
            continue
        kind, data = message
        if kind == "execution_interrupted":
            return "cancelled", None
        if kind != "execution_error":
            continue
        data = data if isinstance(data, dict) else {}
        node_id = str(data.get("node_id") or "")
        node_type = str(data.get("node_type") or "")
        exception_type = str(data.get("exception_type") or "")
        detail = str(data.get("exception_message") or "").splitlines()
        summary = detail[0][:512] if detail else exception_type
        location = f"{node_type} (node {node_id})" if node_id else node_type
        return "error", AIError(
            class_=ErrorClass.BACKEND,
            title="ComfyUI job failed",
            detail=f"{location}: {summary}" if location else summary,
            raw={
                "job_id": job_id,
                "node_id": node_id,
                "node_type": node_type,
                "exception_type": exception_type,
            },
        )
    label = _history_status_label(status)
    if label in {"error", "failed"} or record.get("error"):
        return "error", AIError(
            class_=ErrorClass.BACKEND,
            title="ComfyUI job failed",
            detail="The workflow failed before all outputs were generated.",
            raw={"job_id": job_id},
        )
    if label == "cancelled":
        return "cancelled", None
    if (
        status_fields.get("completed") is False
        or record.get("completed") is False
        or label in {"pending", "running", "queued", "processing", "executing"}
    ):
        return "pending", None
    return "done", None
