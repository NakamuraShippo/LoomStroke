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
    _HistoryTerminalError,
    _PendingHistoryEvent,
    _PendingHistoryProgress,
    history_completion,
)
from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent
from loomstroke.plugins.sdk.base import JobResult, ProgressEvent

from .job_cancellation import _cancel_local_job

logger = logging.getLogger(__name__)


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
    # A node reaching its own maximum is not a terminal hint while the stream
    # stays open. Only normal EOF makes the existing history fallback necessary.
    if saw_full_progress:
        yield await _terminal_progress(service, job_id, prompt_id, job)


async def _terminal_progress(
    service: Any, job_id: str, prompt_id: str, job: _JobConnection | None
) -> ProgressEvent:
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
