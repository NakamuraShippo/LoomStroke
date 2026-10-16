"""Classify ComfyUI history before publishing a terminal generation result."""

from __future__ import annotations

from typing import Any

from loomstroke.plugins.ai_effect.errors import AIError, ErrorClass


def history_completion(history: Any, *, job_id: str) -> tuple[str, AIError | None]:
    """Read terminal status, keeping legacy records without status compatible.

    A history can already contain images when a later node fails. Its outputs
    are therefore insufficient evidence that the whole workflow completed.
    Never retain the prompt, current inputs, or traceback in the error payload.
    """
    record = history if isinstance(history, dict) else {}
    if not any(key in record for key in ("outputs", "status", "prompt")):
        record = record.get(job_id, {})
    status = record.get("status") if isinstance(record, dict) else None
    if not isinstance(status, dict):
        return "done", None

    messages = status.get("messages")
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
    if str(status.get("status_str") or "").lower() in {"error", "failed"}:
        return "error", AIError(
            class_=ErrorClass.BACKEND,
            title="ComfyUI job failed",
            detail="The workflow failed before all outputs were generated.",
            raw={"job_id": job_id},
        )
    if status.get("completed") is False:
        return "pending", None
    return "done", None
