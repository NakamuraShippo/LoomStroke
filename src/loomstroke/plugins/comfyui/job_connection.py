"""Private connection ownership for one standard ComfyUI submission."""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import AsyncIterator
from dataclasses import dataclass
from inspect import getattr_static
from typing import Any

import aiohttp

from loomstroke.core.events import Events, JobProgressEvent, JobResultEvent
from loomstroke.plugins.ai_effect.errors import AIError
from loomstroke.plugins.common.comfyui_history import (
    _HISTORY_UNAVAILABLE_ERROR,
    _HistoryTerminalError,
    _PendingHistoryEvent,
    _PendingHistoryProgress,
    _raise_terminal_failure,
    history_completion,
)
from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent
from loomstroke.plugins.sdk.base import JobResult, ProgressEvent

from .job_cancellation import _cancel_local_job, _JobTaskCleanup
from .job_queue_presence import QueueAbsence, queue_reader

logger = logging.getLogger(__name__)
_HISTORY_POLL_INITIAL_SEC = 0.5
_HISTORY_POLL_MAX_SEC = 2.0
_QUEUE_POLL_INTERVAL_SEC = 5.0
_QUEUE_ABSENCE_GRACE_SEC = 30.0
_UNMAPPED_HISTORY_TIMEOUT_SEC = 60.0


class _JobConnectionError(RuntimeError):
    """A bound operation cannot safely continue through another route."""


def _standard_job_service(service: Any) -> bool:
    from .extended_service import _JOB_CONNECTION_DEFAULTS as extended  # noqa: PLC0415
    from .service import _JOB_CONNECTION_DEFAULTS as base  # noqa: PLC0415

    return all(
        getattr_static(service, name, None) is method for name, method in (base | extended).items()
    )


@dataclass
class _JobConnection:
    """Runtime-only reference shared by the job's tasks and result handle."""

    service: Any
    connection: Any
    _gateway_history_owner: bool = False
    _terminal_failure: tuple[str, AIError | None] | None = None
    _tasks_bound: bool = False
    _stream_task: weakref.ReferenceType[asyncio.Task] | None = None
    _monitor_task: weakref.ReferenceType[asyncio.Task] | None = None
    _output_history_wait: tuple[asyncio.Timeout, float] | None = None

    def require(self) -> Any:
        if not _standard_job_service(self.service):
            raise _JobConnectionError("ComfyUI job implementation changed")
        if not ensure_network_services_consent(self.service._settings, service_name="ComfyUI"):
            raise _JobConnectionError("ComfyUI access is disabled")
        if self.connection is None:
            raise _JobConnectionError("ComfyUI connection unavailable")
        if getattr_static(self.connection, "_intentional_disconnect", None) is not False:
            raise _JobConnectionError("ComfyUI job connection was disconnected")
        return self.connection

    def select_if_needed(self) -> None:
        # A public submission may first initialise its connection during validation.
        # Gateway submissions already supplied theirs before upload/connect awaits.
        if self.connection is None:
            self.connection = self.service._connection
        self.require()

    def defer_terminal_history_to_monitor(self) -> bool:
        """Give the monitor the recovery window or leave its completed result alone."""
        if self._output_history_wait is None:
            return self._monitor_task is not None
        timer, seconds = self._output_history_wait
        if timer.when() is None:
            timer.reschedule(asyncio.get_running_loop().time() + seconds)
        return True

    def stop_progress_stream(self) -> None:
        task = self._stream_task() if self._stream_task is not None else None
        if task is not None and task is not asyncio.current_task() and not task.done():
            # Give a just-created stream a chance to install its tracking cleanup.
            asyncio.get_running_loop().call_soon(task.cancel)


def _capture_job_connection(service: Any, connection: Any) -> _JobConnection | None:
    if not _standard_job_service(service):
        return None
    if (
        connection is not None
        and type(getattr_static(connection, "_intentional_disconnect", None)) is not bool
    ):
        return None
    return _JobConnection(service, connection)


def _job_connection(service: Any, job: _JobConnection | None) -> Any:
    return job.require() if job is not None else service._connection


async def _read_terminal_history(
    service: Any, job_id: str, job: _JobConnection | None
) -> tuple[Any, str, AIError | None] | None:
    history = await _job_connection(service, job).get_history(job_id)
    _raise_terminal_failure(job)
    if job is not None:
        job.require()
    entry = history.get(job_id) if history else None
    if entry:
        kind, error = history_completion(entry, job_id=job_id)
        if kind != "pending":
            return entry, kind, error
    return None


async def _wait_for_output_history(
    service: Any,
    job_id: str,
    job: _JobConnection | None,
    *,
    history_only: bool,
    recovery_timeout: float,
) -> tuple[Any, str, AIError | None]:
    # The mapped-output monitor starts alongside an arbitrarily long queue/run.
    # Its own terminal WebSocket hint can arm a deadline. Without that hint,
    # only sustained, observed absence from the server queue can end the wait.
    timer = asyncio.timeout(recovery_timeout if history_only else None)
    watch = (timer, recovery_timeout)
    absence = QueueAbsence(interval=_QUEUE_POLL_INTERVAL_SEC, grace=_QUEUE_ABSENCE_GRACE_SEC)
    try:
        async with timer:
            if job is not None and not history_only:
                job._output_history_wait = watch
            delay = _HISTORY_POLL_INITIAL_SEC
            while True:
                if history_only and not ensure_network_services_consent(
                    service._settings, service_name="ComfyUI"
                ):
                    raise RuntimeError("ComfyUI access is disabled")
                result = await _read_terminal_history(service, job_id, job)
                if result is not None:
                    return result
                if (
                    job is not None
                    and timer.when() is None
                    and await absence.confirmed(job.require(), job_id)
                ):
                    # History may become visible while the last queue read is in flight.
                    result = await _read_terminal_history(service, job_id, job)
                    if result is not None:
                        return result
                    if timer.when() is None:
                        raise RuntimeError(_HISTORY_UNAVAILABLE_ERROR)
                await asyncio.sleep(delay)
                delay = min(_HISTORY_POLL_MAX_SEC, delay + _HISTORY_POLL_INITIAL_SEC)
    except TimeoutError as exc:
        if timer.expired():
            _raise_terminal_failure(job)
            raise RuntimeError(_HISTORY_UNAVAILABLE_ERROR) from exc
        raise
    finally:
        if job is not None and job._output_history_wait is watch:
            job._output_history_wait = None


def _validate_workflow_nodes(workflow: Any, get_schema: Any) -> dict[str, Any]:
    errors = []
    for node_id, node in workflow.nodes.items():
        schema = get_schema(node.type)
        if not schema:
            errors.append(f"Unknown node type: {node.type} (id: {node_id})")
            continue
        required = schema.get("input", {}).get("required", {})
        for input_name in required:
            if input_name not in node.inputs:
                errors.append(f"Missing required input '{input_name}' in node {node_id}")
    for link in workflow.links:
        if link.source_node not in workflow.nodes:
            errors.append(f"Link references unknown source node: {link.source_node}")
        if link.target_node not in workflow.nodes:
            errors.append(f"Link references unknown target node: {link.target_node}")
    return {"valid": not errors, "errors": errors, "warnings": []}


async def _prepare_workflow(
    service: Any,
    workflow: Any,
    image_parameters: Any,
    metadata_bindings: Any,
    job: _JobConnection | None,
) -> Any:
    if job is None:
        validation = await service.validate_workflow(workflow)
    else:
        if job.connection is None:
            await service.initialize()
            job.select_if_needed()
        nodes = await job.require().get_object_info()
        job.require()
        validation = _validate_workflow_nodes(workflow, (nodes or {}).get)
    if not validation["valid"]:
        raise ValueError(f"Invalid workflow: {validation['errors']}")
    if image_parameters:
        if job is None:
            await service._process_image_parameters(workflow, image_parameters)
        else:
            await service._process_image_parameters(workflow, image_parameters, _job=job)
    if metadata_bindings:
        service._resolve_metadata_bindings(workflow, metadata_bindings)
    return workflow.to_api_format()


async def _stream_progress(
    service: Any, job_id: str, job: _JobConnection | None = None
) -> AsyncIterator[ProgressEvent]:
    prompt_id = service._resolve_prompt_id(job_id)
    connection = _job_connection(service, job)
    saw_full_progress = False
    async for update in connection.stream_progress(prompt_id):
        if job is not None:
            job.require()
        if update["type"] == "progress":
            saw_full_progress |= update.get("max", 100) > 0 and update.get(
                "value", 0
            ) >= update.get("max", 100)
            yield ProgressEvent(
                job_id=job_id,
                step=update.get("value", 0),
                total=update.get("max", 100),
                message=update.get("message"),
                preview_png=update.get("preview"),
            )
        elif update["type"] == "error":
            kind, error = history_completion(
                {"status": {"messages": [["execution_error", update.get("data")]]}},
                job_id=prompt_id,
            )
            if job is not None and job._terminal_failure is None:
                job._terminal_failure = (kind, error)
            raise _HistoryTerminalError(kind, error)
        elif update["type"] in {"executed", "complete", "execution_success", "execution_complete"}:
            yield await _terminal_progress(service, job_id, prompt_id, job)
            return
    # With a queue-aware monitor, EOF cannot turn a node's maximum into
    # a completion signal. Keep the older fallback for other connections.
    if saw_full_progress and not (
        job is not None and job._monitor_task is not None and callable(queue_reader(connection))
    ):
        yield await _terminal_progress(service, job_id, prompt_id, job)


async def _terminal_progress(
    service: Any, job_id: str, prompt_id: str, job: _JobConnection | None
) -> ProgressEvent:
    if job is not None and job.defer_terminal_history_to_monitor():
        # The mapped-output monitor owns the history request and final event.
        return _PendingHistoryProgress(job_id=job_id, step=0, total=0)
    history = await _job_connection(service, job).get_result(prompt_id)
    if job is not None:
        job.require()
    kind, error = history_completion(history, job_id=prompt_id) if history else ("pending", None)
    if kind == "pending":
        return _PendingHistoryProgress(job_id=job_id, step=0, total=0)
    if kind != "done":
        raise _HistoryTerminalError(kind, error)
    result = await _history_to_result(service, job_id, history, job)
    assert result is not None
    return ProgressEvent(
        job_id=job_id, step=100, total=100, message="Complete", preview_png=result.output
    )


async def _get_job_result(
    service: Any, job_id: str, job: _JobConnection | None = None
) -> JobResult | None:
    result = await _job_connection(service, job).get_result(service._resolve_prompt_id(job_id))
    return await _history_to_result(service, job_id, result, job)


async def _history_to_result(
    service: Any, job_id: str, history: Any, job: _JobConnection | None
) -> JobResult | None:
    if not history:
        return None
    kind, error = history_completion(history, job_id=service._resolve_prompt_id(job_id))
    if kind == "pending":
        return None
    if kind != "done":
        return JobResult(
            job_id=job_id, success=False, error=str(_HistoryTerminalError(kind, error))
        )
    if job is None:
        output = await service._extract_history_result_image(history)
    else:
        output = await service._extract_history_result_image(history, _job=job)
    return JobResult(
        job_id=job_id, success=True, output=output, metadata=history.get("metadata", {})
    )


async def _handle_pending_history(service: Any, job_id: str, job: _JobConnection | None) -> None:
    if job is not None:
        job.require()
    if job is not None and job._gateway_history_owner:
        if service._event_bus:
            service._event_bus.emit(
                Events.AI_JOB_PROGRESS, _PendingHistoryEvent(job_id=job_id, step=0, total=0)
            )
    else:
        if not _standard_job_service(service):
            raise _JobConnectionError("ComfyUI history monitoring implementation unavailable")
        # Standalone callers have no gateway handle to receive the hint.
        # Await the existing monitor inside this same stream task.
        await service._monitor_job_outputs(job_id, {}, {}, _job=job, _history_only=True)


async def _handle_stream_event(
    service: Any, job_id: str, event: ProgressEvent, has_monitor: bool, job: _JobConnection | None
) -> bool:
    if isinstance(event, _PendingHistoryProgress):
        if not has_monitor:
            await _handle_pending_history(service, job_id, job)
        return True
    if not service._event_bus:
        return False
    service._event_bus.emit(
        Events.AI_JOB_PROGRESS,
        JobProgressEvent(
            job_id=job_id,
            step=event.step,
            total=event.total,
            message=event.message,
            preview_data=event.preview_png,
        ),
    )
    if not (event.total > 0 and event.step >= event.total):
        return False
    if has_monitor:
        return False
    history = await _job_connection(service, job).get_result(service._resolve_prompt_id(job_id))
    kind, error = history_completion(history, job_id=job_id) if history else ("pending", None)
    if kind == "pending":
        return False
    if kind != "done":
        raise _HistoryTerminalError(kind, error)
    result = (
        await service.get_result(job_id)
        if job is None
        else await _history_to_result(service, job_id, history, job)
    )
    failed = isinstance(result, JobResult) and not result.success
    service._event_bus.emit(
        Events.AI_JOB_RESULT,
        JobResultEvent(
            job_id=job_id,
            success=not failed,
            result=result,
            result_data=None if failed else history,
            error=result.error if failed else None,
        ),
    )
    return True


def _emit_history_failure(service: Any, job_id: str, failure: _HistoryTerminalError) -> None:
    if service._event_bus:
        event = Events.AI_JOB_CANCELLED if failure.kind == "cancelled" else Events.AI_JOB_RESULT
        service._event_bus.emit(
            event,
            JobResultEvent(
                job_id=job_id,
                success=False,
                result=JobResult(job_id=job_id, success=False, error=str(failure)),
                error=str(failure),
            ),
        )


async def _cancel_job(service: Any, job_id: str, job: _JobConnection | None = None) -> bool:
    return await _cancel_local_job(service, job_id, job)


async def _monitor_unmapped_job(service: Any, job_id: str, job: _JobConnection) -> None:
    """Publish terminal history for Gallery-only and AI Gateway submissions."""
    cleanup = _JobTaskCleanup.capture(service, job_id, monitor=True)
    try:
        entry, kind, error = await _wait_for_output_history(
            service,
            job_id,
            job,
            history_only=False,
            recovery_timeout=_UNMAPPED_HISTORY_TIMEOUT_SEC,
        )
        if kind != "done":
            raise _HistoryTerminalError(kind, error)
        # The Gateway reads selected outputs from the history on its bound connection.
        result = None
        if not job._gateway_history_owner:
            result = await _history_to_result(service, job_id, entry, job)
            if result is not None and result.output:
                entry = {**entry, "_image_bytes": result.output}
        _raise_terminal_failure(job)
        if service._event_bus:
            service._event_bus.emit(
                Events.AI_JOB_RESULT,
                JobResultEvent(job_id=job_id, success=True, result=result, result_data=entry),
            )
    except _HistoryTerminalError as failure:
        _emit_history_failure(service, job_id, failure)
    except Exception as exc:  # noqa: BLE001 - send job failures to the same event consumers
        if service._event_bus:
            error_message = str(exc)
            service._event_bus.emit(
                Events.AI_JOB_RESULT,
                JobResultEvent(job_id=job_id, success=False, result=None, error=error_message),
            )
    finally:
        cleanup.finish()
        job.stop_progress_stream()


def _start_job_tasks(
    service: Any,
    prompt_id: str,
    output_targets: Any,
    model_targets: Any,
    job: _JobConnection | None,
) -> None:
    service._job_prompt_ids[prompt_id] = prompt_id
    if output_targets or model_targets:
        if job is None:
            service._schedule_output_import(prompt_id, output_targets or {}, model_targets or {})
        else:
            job.require()
            service._schedule_output_import(
                prompt_id, output_targets or {}, model_targets or {}, _job=job
            )
    if job is not None and job._monitor_task is None and callable(queue_reader(job.require())):
        monitor = asyncio.create_task(_monitor_unmapped_job(service, prompt_id, job))
        service._output_tasks[prompt_id] = monitor
        job._monitor_task = weakref.ref(monitor)
    if job is None:
        task = asyncio.create_task(service._stream_job(prompt_id, prompt_id))
    else:
        task = asyncio.create_task(service._stream_job(prompt_id, prompt_id, _job=job))
    service._active_jobs[prompt_id] = task
    if job is not None:
        job._tasks_bound = True
        job._stream_task = weakref.ref(task)


def _log_workflow_inputs(api_workflow: Any) -> None:
    for node_id, node_data in api_workflow.items():
        if node_data.get("class_type") != "LoadImage" or "image" not in node_data.get("inputs", {}):
            continue
        image_path = node_data["inputs"]["image"]
        if isinstance(image_path, str) and image_path.startswith("layer_"):
            logger.info("Layer reference detected in node %s: %s", node_id, image_path)
        elif isinstance(image_path, str) and not image_path.startswith(("/", "C:\\")):
            logger.debug("ComfyUI file reference in node %s: %s", node_id, image_path)


async def _submit_workflow(  # noqa: PLR0913 - existing submission arguments
    service: Any,
    workflow: Any,
    client_id: str | None = None,
    image_parameters: Any = None,
    output_targets: Any = None,
    model_output_targets: Any = None,
    metadata_bindings: Any = None,
    *,
    job: _JobConnection | None = None,
) -> str:
    if not ensure_network_services_consent(service._settings, service_name="ComfyUI"):
        raise RuntimeError(
            "ComfyUI access is disabled. "
            "Set 'privacy/allow_external_services' to true to enable."
        )
    api_workflow = await _prepare_workflow(
        service, workflow, image_parameters, metadata_bindings, job
    )
    _log_workflow_inputs(api_workflow)
    if job is None:
        await service.initialize()
    connection = _job_connection(service, job)
    if not connection:
        raise RuntimeError("Not connected to ComfyUI")
    try:
        if not connection.is_connected():
            logger.warning("Connection lost, attempting to reconnect...")
            await _job_connection(service, job).connect()
            if job is not None:
                job.require()
            connection = _job_connection(service, job)
            if not connection.is_connected():
                raise RuntimeError("Failed to reconnect to ComfyUI")
    except Exception as exc:
        logger.error("Connection check failed: %s", exc)
        raise RuntimeError(f"ComfyUI connection error: {exc}") from exc
    try:
        prompt_id = await _job_connection(service, job).queue_prompt(api_workflow, client_id)
        if not prompt_id:
            raise RuntimeError("Failed to queue workflow")
        _start_job_tasks(service, prompt_id, output_targets, model_output_targets, job)
        return prompt_id
    except aiohttp.ClientConnectionError as exc:
        logger.error("Connection error during submission: %s", exc)
        raise RuntimeError("Lost connection to ComfyUI during workflow submission") from exc
    except Exception as exc:
        logger.error("Failed to submit workflow: %s", exc)
        raise RuntimeError(f"Workflow submission failed: {exc}") from exc
