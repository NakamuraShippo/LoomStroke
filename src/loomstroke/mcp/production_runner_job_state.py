"""Publish terminal production job status to runtime and the durable store."""

from __future__ import annotations

import logging
from typing import Any, Literal

from .types import JobRecord

logger = logging.getLogger(__name__)


def finish_runner_job(
    store: Any,
    runtime: JobRecord,
    job: dict[str, Any],
    status: Literal["cancelled", "failed"],
    *,
    error: dict[str, Any] | None = None,
) -> None:
    runtime.status = status
    runtime.progress = None
    runtime.touch()
    job.update(status=status, updatedAt=runtime.updated_at)
    if status == "cancelled":
        job["progress"] = None
    if error is not None:
        job["error"] = error
        runtime.metadata["error"] = dict(error)
    try:
        store.add_job(job)
    except OSError as exc:
        logger.warning(
            "Could not persist production job %s as %s (%s)",
            runtime.job_id,
            status,
            type(exc).__name__,
        )


def cancel_queued_runner_job(store: Any, runtime: JobRecord, job: dict[str, Any]) -> bool:
    """Record cancellation when the task has not entered its own cleanup path."""
    runtime.cancel = None
    if runtime.status != "queued":
        return False
    finish_runner_job(store, runtime, job, "cancelled")
    return True
