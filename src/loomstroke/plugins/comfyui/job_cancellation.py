"""Request local job shutdown without waiting for remote interruption."""

from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass
from inspect import getattr_static
from typing import Any

from loomstroke.core.events import Events, JobResultEvent
from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent

_ABSENT = object()
_MAP_NAMES = (
    "_active_jobs",
    "_output_tasks",
    "_output_targets",
    "_model_output_targets",
    "_job_prompt_ids",
)


def _entries(service: Any, job_id: str, names: tuple[str, ...]) -> dict[str, tuple[dict, Any]]:
    stored = vars(service)
    return {
        name: (mapping, mapping.get(job_id, _ABSENT))
        for name in names
        if isinstance(mapping := stored.get(name), dict)
    }


def _remove_owned(service: Any, job_id: str, entries: dict[str, tuple[dict, Any]]) -> None:
    for name, (mapping, value) in entries.items():
        if vars(service).get(name) is mapping and mapping.get(job_id, _ABSENT) is value:
            mapping.pop(job_id, None)


def _task(value: Any) -> asyncio.Task | None:
    return value if isinstance(value, asyncio.Task) else None


def _bound_task(job: Any, name: str) -> asyncio.Task | None:
    reference = getattr_static(job, name, None)
    task = reference() if isinstance(reference, weakref.ReferenceType) else None
    return task if isinstance(task, asyncio.Task) else None


@dataclass
class _LocalCancellation:
    service: Any
    job_id: str
    entries: dict[str, tuple[dict, Any]]
    stream: asyncio.Task | None
    monitor: asyncio.Task | None
    stale: bool
    bound: bool

    @classmethod
    def capture(cls, service: Any, job_id: str, job: Any) -> _LocalCancellation:
        entries = _entries(service, job_id, _MAP_NAMES)
        current_stream = entries.get("_active_jobs", ({}, _ABSENT))[1]
        current_monitor = entries.get("_output_tasks", ({}, _ABSENT))[1]
        bound = job is not None and getattr_static(job, "_tasks_bound", False) is True
        stream_owner = _bound_task(job, "_stream_task") if bound else _task(current_stream)
        monitor_owner = _bound_task(job, "_monitor_task") if bound else _task(current_monitor)
        stale = bound and any(
            current is not _ABSENT and current is not expected
            for current, expected in (
                (current_stream, stream_owner),
                (current_monitor, monitor_owner),
            )
        )
        # An original task may remain alive after the visible map was replaced.
        # Stop that task, but remove entries only if the map still belongs to it.
        owned_names = set()
        if current_stream is stream_owner and stream_owner is not None:
            owned_names.update(("_active_jobs", "_job_prompt_ids"))
        if current_monitor is monitor_owner and monitor_owner is not None:
            owned_names.update(("_output_tasks", "_output_targets", "_model_output_targets"))
        owned = {name: value for name, value in entries.items() if name in owned_names}
        stream = stream_owner if not bound or (stream_owner and not stream_owner.done()) else None
        monitor = (
            monitor_owner if not bound or (monitor_owner and not monitor_owner.done()) else None
        )
        return cls(service, job_id, owned, stream, monitor, stale, bound)

    def stop(self) -> bool:
        current = asyncio.current_task()
        for task in (self.stream, self.monitor):
            if task is not None and task is not current and not task.done():
                task.cancel()
        _remove_owned(self.service, self.job_id, self.entries)
        self.entries.clear()
        requested = self.stream is not None or self.monitor is not None
        if requested and not self.stale:
            _emit_cancelled(self.service, self.job_id)
            return True
        return False


def _emit_cancelled(service: Any, job_id: str) -> None:
    bus = getattr(service, "_event_bus", None)
    if bus:
        bus.emit(
            Events.AI_JOB_CANCELLED,
            JobResultEvent(
                job_id=job_id, success=False, error="Job cancelled by user", result=None
            ),
        )


async def _cancel_local_job(service: Any, job_id: str, job: Any = None) -> bool:
    local = _LocalCancellation.capture(service, job_id, job)
    notified = local.stop()
    cancelled = local.stream is not None
    if not ensure_network_services_consent(service._settings, service_name="ComfyUI"):
        return False
    if job is None:
        await service.initialize()
    connection = job.require() if job is not None else service._connection
    await connection.interrupt()
    if not notified and not local.stale and not local.bound:
        # Preserve the legacy no-local-task notification after remote success.
        _emit_cancelled(service, job_id)
    return cancelled


@dataclass
class _JobTaskCleanup:
    service: Any
    job_id: str
    task: asyncio.Task | None
    owner_name: str
    owner_value: Any
    entries: dict[str, tuple[dict, Any]]

    @classmethod
    def capture(cls, service: Any, job_id: str, *, monitor: bool = False) -> _JobTaskCleanup:
        names = (
            ("_output_tasks", "_output_targets", "_model_output_targets")
            if monitor
            else ("_active_jobs", "_job_prompt_ids")
        )
        return cls(
            service,
            job_id,
            asyncio.current_task(),
            names[0],
            vars(service).get(names[0], _ABSENT),
            _entries(service, job_id, names),
        )

    def _is_current(self) -> bool:
        owner = self.entries.get(self.owner_name)
        if owner is None:
            return vars(self.service).get(self.owner_name, _ABSENT) is self.owner_value
        return (
            vars(self.service).get(self.owner_name) is owner[0]
            and ((self.task is not None and owner[1] is self.task) or owner[1] is _ABSENT)
            and owner[0].get(self.job_id, _ABSENT) is owner[1]
        )

    def finish(self) -> None:
        if self._is_current():
            _remove_owned(self.service, self.job_id, self.entries)

    def _cancelled_without_replacement(self) -> bool:
        owner = self.entries.get(self.owner_name)
        return (
            owner is not None
            and self.task is not None
            and owner[1] is self.task
            and self.task.cancelling() > 0
            and vars(self.service).get(self.owner_name) is owner[0]
            and owner[0].get(self.job_id, _ABSENT) is _ABSENT
        )

    def finish_stream(self) -> None:
        from .service import _JOB_TRACKING_CLEANUP_DEFAULT  # noqa: PLC0415

        if (
            getattr_static(self.service, "_cleanup_job_tracking", None)
            is _JOB_TRACKING_CLEANUP_DEFAULT
        ):
            self.finish()
        elif self._is_current() or self._cancelled_without_replacement():
            # A custom private hook retains its existing one-positional call.
            self.service._cleanup_job_tracking(self.job_id)
