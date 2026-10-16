"""ComfyUI Gateway — :class:`AIGateway` adapter over the ComfyUI plugin service.

Spec reference: ``AIeffect_design/04_python_design/gateway_protocol.md`` §7.

Design choices (post-deduplication refactor):

- **Delegate runtime to ExtendedComfyUIService**: the ComfyUI plugin
  already owns the HTTP / WebSocket / privacy / validation / reconnect
  machinery. This gateway no longer duplicates any of that — ``submit``
  patches slot images + params into the workflow and hands off to
  ``ExtendedComfyUIService.submit_workflow``. The returned job is
  wrapped in :class:`_ServiceBackedJobHandle` which bridges the
  service's event-bus streaming back into the ``AIGateway`` /
  ``ProgressEvent`` contract the controller expects.

- **Authoring surface stays here**: ``load_workflow_file`` (with
  UI/API-format normalisation), ``detect_candidates``,
  ``apply_author_choices``, ``parse_author_choices``,
  ``validate_gateway_file`` are the Effect Editor's interface to
  ComfyUI-specific concepts (nodes, inputs, LoadImage / SaveImage).
  ExtendedComfyUIService has no equivalent — those are preset-author
  concerns, not runtime concerns.

- **No QObject / Signal**: Gateway methods return async iterators /
  coroutines. UI concerns (toast, progress bar) live one layer up in
  ``AIEffectGenerationController`` (and, now, the Effects Panel).

- **Non-destructive on legacy plugin**: we read from but never mutate
  the ComfyUI plugin's service. The only exposure is
  ``ComfyUIGateway(service)``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import struct
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from loomstroke.core.events import Events
from loomstroke.plugins.ai_effect.errors import AIError, AIGatewayError, ErrorClass
from loomstroke.plugins.ai_effect.gateway_protocol import (
    AIGateway,
    AuthorChoices,
    CandidateSet,
    DependencyReport,
    ExecutionRequest,
    GatewayStatus,
    GroupChoice,
    JobHandle,
    JobResult,
    MissingDependency,
    OutputCandidate,
    OutputChoice,
    OutputImage,
    OutputModel,
    ParamCandidate,
    ParamChoice,
    ProgressEvent,
    SlotCandidate,
    SlotChoice,
    ValidationResult,
    WorkflowLoadResult,
)
from loomstroke.plugins.ai_effect.gateways.comfyui_history import history_completion
from loomstroke.plugins.comfyui.object_info_spec import combo_choices, is_combo_head
from loomstroke.plugins.comfyui.session_target import active_url, bind_service, confirm

logger = logging.getLogger(__name__)


# =====================================================================
# _ServiceBackedJobHandle
# =====================================================================


class _ServiceBackedJobHandle(JobHandle):
    """Bridge between ExtendedComfyUIService events and the AIGateway contract.

    The service emits :class:`loomstroke.core.events.JobProgressEvent` /
    :class:`JobResultEvent` / cancel events on the host event bus as the
    ComfyUI backend makes progress. This handle subscribes to those events
    filtered by ``prompt_id`` and forwards the translated
    :class:`ProgressEvent` values into an ``asyncio.Queue`` that
    :meth:`progress` iterates.

    Output fetching (final PNG bytes) happens in :meth:`wait` by reusing
    the service's connection to call ``get_image`` for each declared
    output node — the same endpoint the old direct-HTTP path used.
    """

    def __init__(
        self,
        *,
        service: Any,
        request: ExecutionRequest,
        prompt_id: str,
        output_node_ids: list[str],
        output_internal_ids: dict[str, str],
    ) -> None:
        self.job_id = prompt_id
        self.request_id = request.request_id
        self._service = service
        self._request = request
        self._output_node_ids = list(output_node_ids)
        self._output_internal_ids = dict(output_internal_ids)
        self._progress_consumed = False
        self._cancelled = False
        self._progress_queue: asyncio.Queue[ProgressEvent] = asyncio.Queue()
        self._result_data: Any = None
        self._terminal_error: AIError | None = None
        # Strong refs for the EventBus weakref subscriptions.
        self._progress_handler: Any = None
        self._result_handler: Any = None
        self._cancelled_handler: Any = None
        self._subscriptions: list[Any] = []
        # Background poll task for the premature-completion recovery path
        # (see ``_on_result`` / ``_poll_history_until_ready``). Kept as a
        # strong ref so asyncio doesn't garbage-collect the coroutine mid
        # flight, and so ``cancel`` can abort an in-flight poll.
        self._poll_task: asyncio.Task[None] | None = None
        self._subscribe_events()
        # Seed a queued event so the first progress() iteration has
        # something to yield before the first service event lands.
        self._progress_queue.put_nowait(ProgressEvent(kind="queued"))

    # ------------------------------------------------------------------
    # Event subscription
    # ------------------------------------------------------------------

    def _subscribe_events(self) -> None:
        """Subscribe to the service's event bus using closures.

        ``EventBus.on`` stores handlers as ``weakref.ref(handler)``. Python's
        weakref semantics for **bound methods** are treacherous: a bound
        method is created fresh on attribute access and is typically
        garbage collected immediately after being referenced (see
        Python docs — "Weak references to bound methods must be managed
        carefully. ... holding a weak reference to one with
        weakref.ref() alone is not enough — the bound method is
        typically garbage collected immediately"). ``weakref.WeakMethod``
        is the recommended tool for bound methods; plain ``weakref.ref``
        silently turns into a dead ref.

        The ComfyUI panel sidesteps this by subscribing with a closure
        (see ``ComfyUIMainPanel._make_job_event_handler``). We mirror
        that pattern here — closures are normal function objects, not
        bound methods, so ``weakref.ref`` behaves and our handle
        actually receives AI_JOB_PROGRESS / AI_JOB_RESULT events.

        Symptom before the fix: the handle logged "subscribed to bus=..."
        but no progress / result events ever fired, because the weakrefs
        the bus stored dereferenced to None on the first dispatch.
        """
        bus = getattr(self._service, "_event_bus", None)
        if bus is None:
            logger.warning(
                "_ServiceBackedJobHandle: service has no event_bus; "
                "progress streaming disabled for %s",
                self.job_id,
            )
            return
        subscribe = getattr(bus, "subscribe", None) or getattr(bus, "on", None)
        if not callable(subscribe):
            logger.warning(
                "_ServiceBackedJobHandle: event bus %s exposes no subscribe/on",
                type(bus).__name__,
            )
            return

        # Closures, not bound methods — these get strong-held below.
        def progress_handler(payload: Any) -> None:
            self._on_progress(payload)

        def result_handler(payload: Any) -> None:
            self._on_result(payload)

        def cancelled_handler(payload: Any) -> None:
            self._on_cancelled(payload)

        self._progress_handler = progress_handler
        self._result_handler = result_handler
        self._cancelled_handler = cancelled_handler
        self._subscriptions.append(subscribe(Events.AI_JOB_PROGRESS, progress_handler))
        self._subscriptions.append(subscribe(Events.AI_JOB_RESULT, result_handler))
        self._subscriptions.append(subscribe(Events.AI_JOB_CANCELLED, cancelled_handler))
        logger.info(
            "_ServiceBackedJobHandle: subscribed to bus=%s for job=%s",
            type(bus).__name__,
            self.job_id,
        )

    def _on_progress(self, event: Any) -> None:
        evt_job_id = getattr(event, "job_id", "")
        if evt_job_id != self.job_id:
            # Diagnostic: cross-job progress leaks should be rare; logging
            # at DEBUG helps trace the "generation never finishes" path
            # when the service emits with a different job_id shape than
            # what submit_workflow returned.
            logger.debug(
                "_ServiceBackedJobHandle: ignoring progress for other job " "(evt=%r self=%r)",
                evt_job_id,
                self.job_id,
            )
            return
        step = int(getattr(event, "step", 0) or 0)
        total = int(getattr(event, "total", 0) or 0)
        progress = (step / total) if total > 0 else None
        logger.debug(
            "_ServiceBackedJobHandle: progress job=%s step=%d/%d",
            self.job_id,
            step,
            total,
        )
        self._progress_queue.put_nowait(
            ProgressEvent(
                kind="progress",
                progress=progress,
                step=step if step else None,
                max_step=total if total else None,
                message=getattr(event, "message", None),
                preview_png=getattr(event, "preview_data", None),
            )
        )

    def _on_result(self, event: Any) -> None:
        evt_job_id = getattr(event, "job_id", "")
        if evt_job_id != self.job_id:
            logger.debug(
                "_ServiceBackedJobHandle: ignoring result for other job " "(evt=%r self=%r)",
                evt_job_id,
                self.job_id,
            )
            return
        success = bool(getattr(event, "success", False))
        result_data = getattr(event, "result_data", None)
        logger.info(
            "_ServiceBackedJobHandle: result received job=%s success=%s has_data=%s",
            self.job_id,
            success,
            result_data is not None,
        )
        if not success:
            err_msg = getattr(event, "error", "") or "unknown"
            self._terminal_error = AIError(
                class_=ErrorClass.BACKEND,
                title="ComfyUI job failed",
                detail=str(err_msg),
                raw={"job_id": self.job_id},
            )
            self._progress_queue.put_nowait(ProgressEvent(kind="error"))
            return
        if result_data is not None and self._accept_history(result_data):
            return
        # Premature-completion recovery path (absent or still-running history).
        # ``ExtendedComfyUIService._stream_job``
        # decides a job is done on the first progress event whose
        # ``step >= total`` — a heuristic that trips on intermediate node
        # progress (e.g. KSampler's final step) before ComfyUI's
        # ``/history/{prompt_id}`` endpoint is populated. When that happens
        # the event we just received carries ``success=True`` but
        # ``result_data=None`` (observed in dog-food: ``has_data=False``
        # immediately followed by ComfyUI's "Execution completed" ~300 ms
        # later). Fall back to polling ``/history`` ourselves before we
        # signal ``done`` so the ``wait()`` side sees populated data.
        if self._poll_task is not None:
            # Already polling from an earlier premature result event —
            # don't stack tasks.
            return
        logger.info(
            "_ServiceBackedJobHandle: premature completion for %s — "
            "starting /history poll fallback",
            self.job_id,
        )
        try:
            loop = asyncio.get_event_loop()
            self._poll_task = loop.create_task(self._poll_history_until_ready())
        except RuntimeError:
            # No running loop — very unlikely in the production code path
            # (events are dispatched from within the service's loop). Fall
            # back to an immediate terminal error so wait() doesn't hang.
            self._terminal_error = AIError(
                class_=ErrorClass.BACKEND,
                title="Premature completion without event loop",
                detail="Cannot poll /history without a running event loop",
                raw={"job_id": self.job_id},
            )
            self._progress_queue.put_nowait(ProgressEvent(kind="error"))

    async def _poll_history_until_ready(
        self, *, timeout: float = 60.0, interval: float = 0.3
    ) -> None:
        """Poll ``connection.get_result(prompt_id)`` until history is terminal.

        Terminates the progress stream with ``done`` on success, ``error``
        on timeout / connection loss, or ``cancelled`` if cancelled while
        polling. Silent on exceptions from ``get_result`` — those are
        treated as "try again" since ComfyUI's history endpoint may
        briefly 404 between "job finished" and "history persisted".
        """
        connection = self._get_connection()
        if connection is None:
            self._terminal_error = AIError(
                class_=ErrorClass.CONNECTION,
                title="ComfyUI connection unavailable for history poll",
                raw={"job_id": self.job_id},
            )
            self._progress_queue.put_nowait(ProgressEvent(kind="error"))
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        attempts = 0
        while loop.time() < deadline:
            if self._cancelled or self._terminal_error is not None or self._result_data is not None:
                return
            attempts += 1
            try:
                history = await connection.get_result(self.job_id)
            except Exception as exc:  # noqa: BLE001 - tolerate transient failures
                logger.debug(
                    "_ServiceBackedJobHandle: history poll attempt %d raised: %s",
                    attempts,
                    exc,
                )
                history = None
            if history and self._accept_history(history):
                logger.info(
                    "_ServiceBackedJobHandle: history terminal for %s after %d poll(s)",
                    self.job_id,
                    attempts,
                )
                return
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return
        logger.warning(
            "_ServiceBackedJobHandle: history poll timed out for %s after %d attempts",
            self.job_id,
            attempts,
        )
        self._terminal_error = AIError(
            class_=ErrorClass.BACKEND,
            title="ComfyUI history never became available",
            detail=(
                f"Polled /history/{self.job_id} for {timeout:.0f}s without success. "
                "The job may have failed silently on the backend."
            ),
            raw={"job_id": self.job_id},
        )
        self._progress_queue.put_nowait(ProgressEvent(kind="error"))

    def _accept_history(self, history: Any) -> bool:
        """Publish a terminal event only after checking the workflow status."""
        kind, error = history_completion(history, job_id=self.job_id)
        if kind == "pending":
            return False
        if kind == "error":
            self._terminal_error = error
        elif kind == "cancelled":
            self._cancelled = True
        else:
            self._result_data = history
        self._progress_queue.put_nowait(ProgressEvent(kind=kind))
        return True

    def _on_cancelled(self, event: Any) -> None:
        if getattr(event, "job_id", "") != self.job_id:
            return
        self._cancelled = True
        self._progress_queue.put_nowait(ProgressEvent(kind="cancelled"))

    # ------------------------------------------------------------------
    # JobHandle interface
    # ------------------------------------------------------------------

    async def progress(self) -> AsyncIterator[ProgressEvent]:
        if self._progress_consumed:
            raise RuntimeError("progress() already consumed for this handle")
        self._progress_consumed = True
        while True:
            ev = await self._progress_queue.get()
            yield ev
            if ev.kind in ("done", "error", "cancelled"):
                return

    async def wait(  # noqa: PLR0911, PLR0912 - output-extraction guard chain
        self,
    ) -> JobResult:
        if self._cancelled:
            return JobResult(status="cancelled", output_images=[], duration_sec=0.0)
        if self._terminal_error is not None:
            return JobResult(
                status="failed",
                output_images=[],
                duration_sec=0.0,
                error=self._terminal_error,
            )
        if self._result_data is None:
            # Progress stream terminated without populating result_data.
            # This should be unreachable now that ``_on_result`` defers
            # the ``done`` event until /history is polled, but keep the
            # guard so a future change to the event-dispatch order fails
            # loudly instead of handing callers an empty JobResult.
            return JobResult(
                status="failed",
                output_images=[],
                duration_sec=0.0,
                error=AIError(
                    class_=ErrorClass.BACKEND,
                    title="wait() called before progress completed",
                    detail="Caller must drain progress() or wait for terminal event first",
                    raw={"job_id": self.job_id},
                ),
            )
        outputs = _extract_outputs(
            history=self._result_data,
            job_id=self.job_id,
            output_node_ids=self._output_node_ids,
            output_internal_ids=self._output_internal_ids,
        )
        model_outputs = _extract_model_outputs(
            history=self._result_data,
            job_id=self.job_id,
            output_node_ids=self._output_node_ids,
            output_internal_ids=self._output_internal_ids,
        )
        if not outputs and not model_outputs:
            # Diagnostic: the poll succeeded and we have a history record
            # but no image files were discovered against the declared
            # output node ids. Common causes:
            #   - The preset's output node_mappings point at a node id
            #     that ComfyUI rewrites during submission (e.g. UI->API
            #     re-id), so the mapping dangles.
            #   - The workflow used a PreviewImage node whose "images"
            #     entries live in a different key than expected.
            #   - The record has an "outputs" dict but it is empty
            #     (generation ran but never emitted).
            record_keys = (
                list(self._result_data.keys())
                if isinstance(self._result_data, dict)
                else type(self._result_data).__name__
            )
            outputs_dict: Any = None
            if isinstance(self._result_data, dict):
                # Same detection as _extract_outputs.
                if any(k in self._result_data for k in ("outputs", "status", "prompt")):
                    outputs_dict = self._result_data.get("outputs")
                else:
                    inner = self._result_data.get(self.job_id)
                    if isinstance(inner, dict):
                        outputs_dict = inner.get("outputs")
            logger.warning(
                "_ServiceBackedJobHandle: no outputs extracted for job=%s "
                "output_node_ids=%r declared=%r record_keys=%r "
                "history_outputs_keys=%r",
                self.job_id,
                self._output_node_ids,
                self._output_internal_ids,
                record_keys,
                list(outputs_dict.keys()) if isinstance(outputs_dict, dict) else outputs_dict,
            )
        connection = self._get_connection()
        if connection is None:
            return JobResult(
                status="failed",
                output_images=[],
                duration_sec=0.0,
                error=AIError(
                    class_=ErrorClass.CONNECTION,
                    title="ComfyUI connection unavailable for output fetch",
                    raw={"job_id": self.job_id},
                ),
            )
        imported_models = 0
        if model_outputs:
            try:
                imported_models = await self._import_3d_model_outputs(model_outputs)
            except Exception as exc:  # noqa: BLE001 - report as terminal result below
                logger.exception("ComfyUI 3D output import failed for job=%s", self.job_id)
                return JobResult(
                    status="failed",
                    output_images=[],
                    duration_sec=0.0,
                    error=AIError(
                        class_=ErrorClass.BACKEND,
                        title="Could not import ComfyUI 3D output",
                        detail=str(exc),
                        raw={"job_id": self.job_id},
                    ),
                    output_models=model_outputs,
                )
        collected: list[OutputImage] = []
        total_files = 0
        for internal_id, files in outputs:
            for f in files:
                total_files += 1
                try:
                    png_bytes = await connection.get_image(
                        filename=f["filename"],
                        subfolder=f.get("subfolder", ""),
                        folder_type=f.get("type", "output"),
                    )
                except (OSError, TimeoutError, KeyError) as exc:
                    logger.warning(
                        "get_image failed for %s (skipping): %s",
                        f.get("filename"),
                        exc,
                    )
                    continue
                width, height = _parse_png_dims(png_bytes)
                collected.append(
                    OutputImage(
                        internal_id=internal_id,
                        png_bytes=png_bytes,
                        width=width,
                        height=height,
                    )
                )
        if total_files > 0 and not collected and imported_models <= 0:
            return JobResult(
                status="failed",
                output_images=[],
                duration_sec=0.0,
                error=AIError(
                    class_=ErrorClass.BACKEND,
                    title="All output downloads failed",
                    raw={"job_id": self.job_id},
                ),
                output_models=model_outputs,
            )
        if model_outputs and imported_models <= 0 and not collected:
            return JobResult(
                status="failed",
                output_images=[],
                duration_sec=0.0,
                error=AIError(
                    class_=ErrorClass.BACKEND,
                    title="No ComfyUI 3D output was imported",
                    detail=(
                        "The backend produced a model artifact, "
                        "but LoomStroke could not import it."
                    ),
                    raw={"job_id": self.job_id},
                ),
                output_models=model_outputs,
            )
        logger.info(
            "_ServiceBackedJobHandle: wait() returning completed job=%s "
            "output_images=%d output_models=%d imported_models=%d total_files=%d",
            self.job_id,
            len(collected),
            len(model_outputs),
            imported_models,
            total_files,
        )
        return JobResult(
            status="completed",
            output_images=collected,
            duration_sec=0.0,
            output_models=model_outputs,
        )

    async def cancel(self) -> None:
        if self._cancelled:
            return
        self._cancelled = True
        # Abort an in-flight /history poll so we don't keep hitting the
        # backend after the user cancelled.
        poll_task = self._poll_task
        if poll_task is not None and not poll_task.done():
            poll_task.cancel()
        try:
            cancel_fn = getattr(self._service, "cancel", None)
            if callable(cancel_fn):
                maybe = cancel_fn(self.job_id)
                if asyncio.iscoroutine(maybe):
                    await maybe
                return
            conn = self._get_connection()
            if conn is not None:
                await conn.interrupt()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ComfyUI cancel failed: %s", exc)

    def _get_connection(self) -> Any:
        return getattr(self._service, "_connection", None)

    async def _import_3d_model_outputs(self, model_outputs: list[OutputModel]) -> int:
        apply_3d = getattr(self._service, "_apply_3d_job_outputs", None)
        if not callable(apply_3d):
            logger.warning("ComfyUI service has no 3D output importer")
            return 0
        history_outputs = _history_outputs_dict(history=self._result_data, job_id=self.job_id)
        if not history_outputs:
            return 0
        from loomstroke.host_api import LayerRef  # noqa: PLC0415
        from loomstroke.plugins.comfyui.output_targets import (  # noqa: PLC0415
            CREATE_NEW_3D_LAYER_ID,
        )

        targets: dict[str, LayerRef] = {}
        for out in model_outputs:
            targets.setdefault(
                out.node_id,
                LayerRef(
                    id=CREATE_NEW_3D_LAYER_ID,
                    name="(Create new 3D layer)",
                    layer_type="3d_create",
                ),
            )
        if not targets:
            return 0
        imported = await apply_3d(self.job_id, history_outputs, targets)
        try:
            return int(imported)
        except (TypeError, ValueError):
            return 0


# =====================================================================
# ComfyUIGateway
# =====================================================================


class ComfyUIGateway(AIGateway):
    """:class:`AIGateway` that authors .lsfx presets and delegates runtime
    to :class:`ExtendedComfyUIService`.

    Authoring responsibilities (``load_workflow_file``, ``detect_candidates``,
    etc.) stay here because they are ComfyUI-workflow-shaped concerns the
    Effect Editor needs. Runtime (``submit`` / ``check_status`` /
    ``check_dependencies``) delegates to the same ``ExtendedComfyUIService``
    the ComfyUI panel plugin owns, eliminating duplicated HTTP / WebSocket /
    privacy-consent plumbing that used to live in this module.
    """

    gateway_id = "comfyui"
    display_name = "ComfyUI"

    def __init__(
        self,
        service: Any,
        *,
        display_name: str | None = None,
        registry: Any = None,
    ) -> None:
        """Accept the ComfyUI plugin's own :class:`ExtendedComfyUIService`.

        Typed ``Any`` to avoid importing Qt at module import time; the
        attribute surface we use is duck-typed (``submit_workflow`` /
        ``initialize`` / ``get_object_info`` / ``_event_bus`` /
        ``_connection`` / ``cancel``).

        Phase 10c (2026-04-30): an optional ``registry`` (typically a
        :class:`loomstroke.plugins.comfyui.server_registry.ComfyUIServerRegistry`)
        feeds per-URL cached connections into the gateway's submit
        path. When provided, ``_resolve_connection_for_request`` looks
        up an existing connection for the preset's target URL instead
        of mutating the service's ``_connection`` slot to a fresh
        instance every swap. The previous Phase 8a-bridge swap
        pattern (``_switch_service_endpoint``) destroyed the previous
        connection on each transition; the registry-backed path
        keeps every server's connection alive so subsequent submits
        return to the right URL deterministically.

        ``registry=None`` keeps the legacy single-server fallback for
        tests that supply a ``FakeComfyUIService`` without setting up
        a registry.
        """
        self._service = service
        self._disposed = False
        self._registry = registry
        if display_name is not None:
            self.display_name = display_name

    # ------------------------------------------------------------------
    # Authoring API
    # ------------------------------------------------------------------

    def load_workflow_file(self, path: str) -> WorkflowLoadResult:
        """Read a ComfyUI workflow JSON file from disk.

        Normalises to **API format** (``{node_id: {class_type, inputs, ...}}``)
        regardless of whether the file on disk is:

        - API format (saved via ComfyUI's "Save (API Format)" — dev-mode
          only).
        - UI format (default "Save" — ``{"nodes": [...], "links": [...]}``).

        Downstream code (``detect_candidates``, ``apply_author_choices``,
        execution) can then assume a uniform shape. The conversion is a
        no-op for already-API-format inputs.
        """
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            return WorkflowLoadResult(ok=False, error=f"tr_ai_workflow_load_ioerror:{exc}")
        try:
            workflow = json.loads(raw)
        except json.JSONDecodeError as exc:
            return WorkflowLoadResult(ok=False, error=f"tr_ai_workflow_load_parse:{exc}")
        if not isinstance(workflow, dict):
            return WorkflowLoadResult(ok=False, error="tr_ai_workflow_load_not_object")
        try:
            object_info = _cached_object_info_for_request(self._service, self._registry, {})
            workflow = _normalise_to_api_format(workflow, object_info)
        except Exception as exc:  # noqa: BLE001 - report through the error channel
            logger.exception("ComfyUIGateway.load_workflow_file: normalise failed for %s", path)
            return WorkflowLoadResult(ok=False, error=f"tr_ai_workflow_load_normalise:{exc}")
        return WorkflowLoadResult(ok=True, workflow=workflow)

    def detect_candidates(
        self,
        workflow: dict[str, Any],
        *,
        object_info: dict[str, Any] | None = None,
    ) -> CandidateSet:
        """Extract slot / parameter / output candidates from a workflow dict.

        Heuristics (spec §7 §detect_candidates):
        - ``LoadImage`` nodes → input slot candidates.
        - Every node input whose value is a scalar (int/float/str/bool)
          → parameter candidate (the Editor lets the author choose which
          to expose).
        - ``PreviewImage`` / ``SaveImage`` / D2 image-save nodes → output candidates.
        - 3D preview / model-export nodes (for example ``Preview3D``
          linked to ``Pixal3DExportGLB``) → output candidates.

        The same node input is never emitted in more than one section:
        when a ``LoadImage.image`` is already promoted to a slot, its
        filename value is NOT also surfaced as a ``param`` candidate
        (which would force the author to see "Input" twice — once
        under Input slots and once under Parameters — and tick the
        wrong one at export time). Same logic for ``SaveImage``'s
        ``images`` / ``filename_prefix`` — those belong with the
        output, not the parameter list.

        ``workflow`` is the *API-format* dict
        ``{node_id: {class_type, inputs, ...}}`` as produced by
        ``WorkflowModel.to_api_format()``.

        ``object_info`` is the wrapped shape returned by
        :meth:`ExtendedComfyUIService.get_object_info` —
        ``{"nodes": {class_type: schema, ...}, "models": {...}}``. When
        supplied, each ``ParamCandidate`` is enriched with the actual
        backend constraints (min / max / step for INT and FLOAT, the
        choice list for combo inputs, the multiline flag for STRING).
        Falls back to value-shape inference when ``object_info`` is
        missing or has no entry for a given class — the dogfood
        complaint that prompted this path: ParamWidget bounds defaulted
        to ``±1,000,000`` regardless of the backend's actual range,
        producing nonsense like a ``seed`` slider that capped at 1M
        when ComfyUI accepts up to ``0xffffffffffffffff``.
        """
        slots: list[SlotCandidate] = []
        params: list[ParamCandidate] = []
        outputs: list[OutputCandidate] = []
        seen_output_refs: set[str] = set()

        # Input keys that are already captured by a slot / output
        # candidate on the same node and must therefore be excluded
        # from the parameter scan to avoid duplicates. Per-class so
        # an unrelated future node with e.g. an ``image`` text field
        # still gets picked up as a parameter.
        input_node_reserved_keys: dict[str, set[str]] = {
            "LoadImage": {"image", "upload"},
        }

        for node_id, node in workflow.items():
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("class_type", ""))
            inputs = node.get("inputs") or {}
            if class_type == "LoadImage":
                slots.append(
                    SlotCandidate(
                        source_hint=f"Node#{node_id} LoadImage.image",
                        suggested_display_name=_derive_slot_name(node),
                        gateway_ref={"node_id": node_id, "input": "image"},
                    )
                )
            output_ref = _resolve_output_candidate_ref(str(node_id), node, workflow)
            if output_ref is not None:
                ref_node_id, source_hint = output_ref
                if ref_node_id not in seen_output_refs:
                    seen_output_refs.add(ref_node_id)
                    outputs.append(
                        OutputCandidate(
                            source_hint=source_hint,
                            suggested_display_name=_derive_output_name(node),
                            gateway_ref={"node_id": ref_node_id},
                        )
                    )
            reserved_keys = set(input_node_reserved_keys.get(class_type, set()))
            if _is_preview_3d_node_type(class_type):
                reserved_keys.update({"model_file", "image"})
            if isinstance(inputs, dict):
                for key, value in inputs.items():
                    if key in reserved_keys:
                        # Already represented as a slot — don't
                        # double-surface it as a param.
                        continue
                    if not _is_exposable_scalar(value):
                        continue
                    # Try to enrich from object_info; fall back to
                    # value-shape inference when no schema is available.
                    schema_type, schema_range, schema_choices, schema_multiline = (
                        _constraints_from_object_info(object_info, class_type, key)
                    )
                    final_type = schema_type or _scalar_type(value)
                    params.append(
                        ParamCandidate(
                            source_hint=f"Node#{node_id} {class_type}.{key}",
                            suggested_display_name=key,
                            suggested_type=final_type,
                            suggested_default=value,
                            suggested_range=schema_range,
                            suggested_choices=schema_choices,
                            gateway_ref={"node_id": node_id, "input": key},
                            suggested_multiline=schema_multiline,
                        )
                    )

        return CandidateSet(input_slots=slots, parameters=params, outputs=outputs)

    def validate_gateway_file(  # noqa: PLR0911
        self, gateway_file: dict[str, Any]
    ) -> ValidationResult:
        """Verify the gateway file produced by ``apply_author_choices``."""
        if not isinstance(gateway_file, dict):
            return ValidationResult(ok=False, reason="not_dict")
        sv = gateway_file.get("schema_version")
        if not isinstance(sv, str):
            return ValidationResult(ok=False, reason="missing_schema_version")
        if sv.split(".", 1)[0] != "1":
            return ValidationResult(
                ok=False, reason="schema_major_mismatch", detail=f"schema_version={sv}"
            )
        workflow = gateway_file.get("workflow")
        if not isinstance(workflow, dict) or not workflow:
            return ValidationResult(ok=False, reason="missing_or_empty_workflow")
        mappings = gateway_file.get("node_mappings")
        if not isinstance(mappings, dict):
            return ValidationResult(ok=False, reason="missing_node_mappings")
        for internal_id, ref in mappings.items():
            if not isinstance(ref, dict) or "node_id" not in ref:
                return ValidationResult(
                    ok=False,
                    reason="mapping_missing_node_id",
                    detail=f"internal_id={internal_id}",
                )
            if str(ref["node_id"]) not in workflow:
                return ValidationResult(
                    ok=False,
                    reason="mapping_dangles",
                    detail=f"internal_id={internal_id} node_id={ref['node_id']}",
                )
        return ValidationResult(ok=True)

    def apply_author_choices(
        self,
        raw_workflow: dict[str, Any] | None,
        choices: AuthorChoices,
    ) -> dict[str, Any]:
        """Build ``gateways/comfyui.json`` from editor selections.

        Code review #6: returns are deep copies so that later mutations to
        the raw workflow or gateway_ref dicts do not leak into the saved
        gateway_file.
        """
        if raw_workflow is None:
            raise ValueError("ComfyUIGateway.apply_author_choices requires raw_workflow")
        node_mappings: dict[str, Any] = {}
        for s in choices.input_slots:
            node_mappings[s.internal_id] = copy.deepcopy(s.gateway_ref)
        for p in choices.parameters:
            node_mappings[p.internal_id] = copy.deepcopy(p.gateway_ref)
        for o in choices.outputs:
            node_mappings[o.internal_id] = copy.deepcopy(o.gateway_ref)
        return {
            "schema_version": "1.0",
            "workflow": copy.deepcopy(raw_workflow),
            "node_mappings": node_mappings,
            "dependencies": _extract_dependencies(raw_workflow),
        }

    def parse_author_choices(
        self,
        raw_workflow: dict[str, Any] | None,
        gateway_file: dict[str, Any],
    ) -> AuthorChoices:
        """Restore editor selections from a saved gateway file.

        Heuristic: ``node_mappings`` prefixed ``slot_``/``param_``/``output_``
        (matching the internal_id namespace the Editor assigns at save time).
        """
        slots: list[SlotChoice] = []
        params: list[ParamChoice] = []
        outputs: list[OutputChoice] = []
        mappings = gateway_file.get("node_mappings") or {}
        if not isinstance(mappings, dict):
            return AuthorChoices(slots, params, outputs, [])
        for internal_id, ref in mappings.items():
            if not isinstance(internal_id, str) or not isinstance(ref, dict):
                continue
            if internal_id.startswith("slot_"):
                slots.append(
                    SlotChoice(
                        internal_id=internal_id,
                        display_name=internal_id,
                        required=False,
                        order=len(slots),
                        gateway_ref=dict(ref),
                    )
                )
            elif internal_id.startswith("output_"):
                outputs.append(
                    OutputChoice(
                        internal_id=internal_id,
                        display_name=internal_id,
                        mode="self" if len(outputs) == 0 else "new_layer_above",
                        overwrite_on_regenerate=True,
                        order=len(outputs),
                        gateway_ref=dict(ref),
                    )
                )
            else:
                params.append(
                    ParamChoice(
                        internal_id=internal_id,
                        display_name=internal_id,
                        default=None,
                        group=None,
                        type="string",
                        range=None,
                        choices=None,
                        required_text=False,
                        order=len(params),
                        gateway_ref=dict(ref),
                    )
                )
        return AuthorChoices(
            input_slots=slots,
            parameters=params,
            outputs=outputs,
            parameter_groups=[GroupChoice(name="", order=0, collapsed=False)] if params else [],
        )

    def extract_raw_workflow(self, gateway_file: dict[str, Any]) -> dict[str, Any] | None:
        workflow = gateway_file.get("workflow")
        return dict(workflow) if isinstance(workflow, dict) else None

    # ------------------------------------------------------------------
    # Runtime API (delegation to ExtendedComfyUIService)
    # ------------------------------------------------------------------

    async def check_status(self) -> GatewayStatus:
        """Return connection status via the service.

        Uses ``service.get_object_info`` as the liveness probe and reads
        ``service._connection.base_url`` for the endpoint display.
        """
        connection = getattr(self._service, "_connection", None)
        endpoint = str(getattr(connection, "base_url", "") or "")
        try:
            info = None
            get_object_info = getattr(self._service, "get_object_info", None)
            if callable(get_object_info):
                info = await get_object_info()
        except Exception as exc:  # noqa: BLE001 - surface via extra
            return GatewayStatus(connected=False, endpoint=endpoint, extra={"error": str(exc)})
        return GatewayStatus(
            connected=info is not None,
            endpoint=endpoint,
            extra={"node_count": len(info) if isinstance(info, dict) else 0},
        )

    async def check_dependencies(self, gateway_file: dict[str, Any]) -> DependencyReport:
        """Compare declared dependencies vs the server's ``object_info`` catalog.

        Models are not verified (see previous code-review note #1).

        Note on ``get_object_info`` shapes: this gateway talks to
        :class:`ExtendedComfyUIService` whose ``get_object_info`` returns
        ``{"nodes": {node_type: schema, ...}, "models": {...}}`` — a
        wrapped shape. Older stand-alone connections returned the node
        dict directly. We accept both to stay robust across service
        refactors; the previous code took ``object_info.keys()`` which
        for the wrapped shape yielded ``{"nodes", "models"}`` and
        incorrectly flagged every declared node as missing (the
        2026-04-25 dogfood regression).
        """
        declared = gateway_file.get("dependencies")
        if not isinstance(declared, dict) or not declared:
            logger.debug("check_dependencies: no dependencies declared; skipping")
            return DependencyReport(ok=True)
        try:
            get_object_info = getattr(self._service, "get_object_info", None)
            if not callable(get_object_info):
                return DependencyReport(ok=True)
            object_info = await get_object_info() or {}
        except (OSError, TimeoutError) as exc:
            raise AIGatewayError(
                AIError(
                    class_=ErrorClass.CONNECTION,
                    title="Could not reach ComfyUI for dependency check",
                    detail=str(exc),
                )
            ) from exc
        available_nodes = _extract_available_node_names(object_info)

        missing: list[MissingDependency] = []
        for node_type in declared.get("custom_nodes", []) or []:
            if node_type not in available_nodes:
                missing.append(
                    MissingDependency(kind="custom_node", name=str(node_type), hint=None)
                )
        return DependencyReport(ok=not missing, missing=missing)

    async def submit(  # noqa: PLR0912, PLR0915 - guard chain for defensive delegation
        self, request: ExecutionRequest
    ) -> JobHandle:
        """Patch the workflow and hand off to ``ExtendedComfyUIService``.

        Flow:
        1. Deep-copy the saved workflow from ``gateway_file.workflow``.
        2. Upload each slot image via the service's connection and
           substitute the returned server-side filename into the LoadImage
           node declared by ``node_mappings[slot_id]``.
        3. Inject parameter values into the nodes declared by
           ``node_mappings[param_id]``.
        4. Wrap the patched dict in :class:`WorkflowModel` and call
           ``service.submit_workflow`` — this is the same entry point the
           ComfyUI panel uses, so privacy consent / validation / streaming
           all come for free.
        5. Return a :class:`_ServiceBackedJobHandle` that subscribes to
           the service's event bus for progress + result.
        """
        if self._disposed:
            raise AIGatewayError(
                AIError(class_=ErrorClass.UNKNOWN, title="Gateway already disposed")
            )
        gateway_file = request.gateway_file
        if not isinstance(gateway_file, dict):
            raise AIGatewayError(
                AIError(class_=ErrorClass.VALIDATION, title="gateway_file must be a dict")
            )
        raw_workflow = gateway_file.get("workflow")
        if not isinstance(raw_workflow, dict) or not raw_workflow:
            raise AIGatewayError(AIError(class_=ErrorClass.VALIDATION, title="missing workflow"))
        mappings = gateway_file.get("node_mappings") or {}
        if not isinstance(mappings, dict):
            mappings = {}

        # Ensure service has a connection object (creates it if the user
        # has never opened the ComfyUI panel — idempotent on reopen).
        try:
            initialize = getattr(self._service, "initialize", None)
            if callable(initialize):
                await initialize()
        except Exception as exc:  # noqa: BLE001 - wrap upstream failure
            raise AIGatewayError(
                AIError(
                    class_=ErrorClass.CONNECTION,
                    title="Could not initialise ComfyUI service",
                    detail=str(exc),
                )
            ) from exc

        # Phase 10c (2026-04-30): registry-backed per-server connection.
        # The legacy Phase 8a-bridge path mutated the service's
        # ``_connection`` slot to a freshly-built connection on every
        # URL transition; subsequent submits with an empty URL inherited
        # whatever URL the previous swap left behind, producing the
        # "wrong server on next preset" bug.
        #
        # When a registry is wired, target URL resolution is:
        #   1. ``gateway_file["endpoint_url"]`` (explicit AI Effect
        #      Manager / preset custom URL)
        #   2. ``gateway_file["target_server_id"]`` (registered server
        #      id like ``"local"`` / ``"external"``)
        #   3. Registry's ``default_server`` connection (= the URL the
        #      user designated as default in Settings, NOT whatever
        #      previous submit happened to swap to)
        # The resolved connection is owned by the registry so swap is
        # O(1) (cache hit) and previous URLs stay alive in the cache
        # for the next submit that needs them.
        #
        # When registry is ``None`` (FakeService tests or pre-Phase-10c
        # boot path) the legacy disconnect/build-fresh path runs to
        # preserve compatibility.
        endpoint_url = gateway_file.get("endpoint_url")
        target_url = (
            endpoint_url.strip() if isinstance(endpoint_url, str) and endpoint_url.strip() else None
        )
        raw_server_id = gateway_file.get("target_server_id")
        target_server_id = (
            raw_server_id.strip()
            if isinstance(raw_server_id, str) and raw_server_id.strip()
            else None
        )
        if self._registry is not None:
            try:
                await self._use_registry_connection_for(target_url, target_server_id)
            except Exception as exc:  # noqa: BLE001 - wrap upstream
                target_label = target_server_id or target_url or "<default>"
                raise AIGatewayError(
                    AIError(
                        class_=ErrorClass.CONNECTION,
                        title="Could not select ComfyUI endpoint",
                        detail=f"target={target_label}: {exc}",
                    )
                ) from exc
        elif target_url is not None:
            try:
                await self._switch_service_endpoint(target_url)
            except Exception as exc:  # noqa: BLE001 - wrap upstream
                raise AIGatewayError(
                    AIError(
                        class_=ErrorClass.CONNECTION,
                        title="Could not switch ComfyUI endpoint",
                        detail=f"endpoint_url={endpoint_url!r}: {exc}",
                    )
                ) from exc

        connection = getattr(self._service, "_connection", None)
        if connection is None:
            raise AIGatewayError(
                AIError(
                    class_=ErrorClass.CONNECTION,
                    title="ComfyUI service is not connected",
                    detail=(
                        "The ComfyUI plugin did not create a connection "
                        "(privacy/allow_external_services disabled or host/port "
                        "not configured). Open the ComfyUI panel once to "
                        "configure the connection before generating."
                    ),
                )
            )
        # Establish the WebSocket when not already connected — otherwise
        # the service's _stream_job cannot deliver AI_JOB_PROGRESS events
        # and the AI Effect handle would hang forever. Mirrors what the
        # ComfyUI panel's Connect button does.
        try:
            is_connected_fn = getattr(connection, "is_connected", None)
            already_connected = bool(is_connected_fn()) if callable(is_connected_fn) else False
            if not already_connected:
                connect_fn = getattr(self._service, "connect", None)
                if callable(connect_fn):
                    await connect_fn()
                else:
                    # Fall back to the connection's own connect if the
                    # service exposes no higher-level helper.
                    inner_connect = getattr(connection, "connect", None)
                    if callable(inner_connect):
                        await inner_connect()
        except Exception as exc:  # noqa: BLE001 - wrap upstream connect failure
            raise AIGatewayError(
                AIError(
                    class_=ErrorClass.CONNECTION,
                    title="Could not connect to ComfyUI",
                    detail=str(exc),
                )
            ) from exc

        if self._registry is not None and callable(is_connected_fn) and is_connected_fn() is True:
            confirm(self._registry, getattr(connection, "base_url", ""))
        patched = copy.deepcopy(raw_workflow)

        # Resolve every slot binding up-front. This does the upload and
        # records the resulting ``(node_id, input_key) -> filename``
        # assignment. The actual workflow write happens *after* the
        # parameter pass so slot bindings always win collisions with
        # parameter entries that target the same node input.
        #
        # Why the collision matters: presets authored with the old
        # ``detect_candidates`` heuristic (fixed in the "duplicate
        # LoadImage.image" regression) saved two mappings pointing at
        # the same (node, input) — one under the slot's internal_id,
        # one under the parameter's. If we let the parameter loop run
        # last (old order), the slot's freshly uploaded filename got
        # immediately overwritten by the preset's stored default
        # (e.g. ``"SketchRush015.png"``), and the user's chosen layer
        # never reached the workflow. Dogfood symptom: "input に
        # レイヤーを指定しても ComfyUI に送信されていない" despite
        # the upload log showing the bytes going out.
        slot_overrides: dict[tuple[str, str], tuple[str, str]] = {}
        for internal_id, slot_image in request.slot_images.items():
            ref = mappings.get(internal_id)
            if not isinstance(ref, dict):
                continue
            node_id = str(ref.get("node_id"))
            input_key = str(ref.get("input", "image"))
            if node_id not in patched:
                continue
            upload_name = f"loomstroke_slot_{internal_id}.png"
            try:
                # Explicit ``subfolder=""`` — land the file in ComfyUI's
                # input/ root so LoadImage's INPUT_TYPES enum (which only
                # lists direct children of input/) surfaces it. Passing
                # any non-empty subfolder here would have ComfyUI save the
                # file at input/<subfolder>/<name>, and LoadImage would
                # then either reject the path during VALIDATE_INPUTS or
                # fall back to a stale default — neither of which shows
                # the author's selected layer content in the output.
                uploaded_filename = await connection.upload_image_bytes(
                    slot_image.png_bytes,
                    upload_name,
                    subfolder="",
                )
            except (OSError, TimeoutError) as exc:
                raise AIGatewayError(
                    AIError(
                        class_=ErrorClass.CONNECTION,
                        title="Could not upload slot image to ComfyUI",
                        detail=str(exc),
                        raw={"internal_id": internal_id},
                    )
                ) from exc
            logger.info(
                "ComfyUIGateway.submit: slot=%s uploaded payload_bytes=%d "
                "sent_name=%r echoed=%r -> will patch %s.inputs[%s]",
                internal_id,
                len(slot_image.png_bytes),
                upload_name,
                uploaded_filename,
                node_id,
                input_key,
            )
            # Snapshot the actual PNG that left LoomStroke into the
            # user-visible logs directory so dogfood can verify by eye
            # what ComfyUI actually received. Common reason "input not
            # applied" survives the technical fixes: the slot ends up
            # bound to a layer that is empty / hidden / off-canvas, and
            # we send a near-blank image — the only way to catch that
            # without an actual canvas screenshot is to inspect the
            # serialised PNG directly.
            _snapshot_slot_png_for_diagnosis(internal_id, slot_image.png_bytes)
            slot_overrides[(node_id, input_key)] = (internal_id, uploaded_filename)

        # Parameter value injection (first pass). Skips any (node_id,
        # input_key) that a slot already claims; the slot pass below
        # would win anyway, but skipping here keeps the diagnostic log
        # readable and avoids a misleading "written then overwritten"
        # trace during bug investigations.
        for internal_id, value in request.param_values.items():
            ref = mappings.get(internal_id)
            if not isinstance(ref, dict):
                continue
            node_id = str(ref.get("node_id"))
            input_key = ref.get("input")
            if node_id not in patched or not isinstance(input_key, str):
                continue
            override = slot_overrides.get((node_id, input_key))
            if override is not None:
                slot_internal_id, _filename = override
                logger.warning(
                    "ComfyUIGateway.submit: param %r and slot %r both map to "
                    "%s.inputs[%s] — slot wins, param value %r is ignored. "
                    "This usually means the preset was authored before the "
                    "LoadImage.image duplicate-detection fix and should be "
                    "re-saved to drop the stale parameter entry.",
                    internal_id,
                    slot_internal_id,
                    node_id,
                    input_key,
                    value,
                )
                continue
            patched[node_id].setdefault("inputs", {})[input_key] = value

        # Slot pass (second / authoritative). Apply the uploaded
        # filenames last so they override any lingering default value
        # the parameter pass might have left on the same input.
        for (node_id, input_key), (_internal_id, filename) in slot_overrides.items():
            patched[node_id].setdefault("inputs", {})[input_key] = filename

        object_info = _cached_object_info_for_request(self._service, self._registry, gateway_file)
        filled_defaults = _fill_missing_required_input_defaults(patched, object_info)
        for node_id, input_key, value in filled_defaults:
            logger.info(
                "ComfyUIGateway.submit: filled missing required input "
                "%s.inputs[%s] from ComfyUI object_info default %r",
                node_id,
                input_key,
                value,
            )

        # Delegate to service (privacy consent + validation + streaming).
        from loomstroke.plugins.comfyui.workflow_model import WorkflowModel  # noqa: PLC0415

        # Diagnostic: dump every LoadImage node's final inputs after
        # all slot/param patching. Lets dogfood verify that the
        # uploaded filename actually made it into the workflow that
        # gets serialised to ComfyUI, separate from the upload itself
        # succeeding. Common surprise: an extra LoadImage in the
        # workflow that the preset never mapped to a slot, so it
        # keeps a stale filename baked in at preset-author time.
        for diag_node_id, diag_node in patched.items():
            if not isinstance(diag_node, dict):
                continue
            if str(diag_node.get("class_type", "")) != "LoadImage":
                continue
            logger.info(
                "ComfyUIGateway.submit: post-patch LoadImage node=%s inputs=%r",
                diag_node_id,
                diag_node.get("inputs"),
            )

        workflow_model = WorkflowModel.from_json(patched)
        # NOTE: do NOT pass ``client_id=request.request_id``. ComfyUI
        # routes WebSocket job events to the client whose ``clientId``
        # matches the one submitted with the prompt. The WebSocket
        # session uses ``connection.client_id`` (a fixed per-connection
        # UUID). Passing our per-request UUID as client_id here would
        # make ComfyUI deliver events to a phantom client — the exact
        # reason progress / result events never reached this handle
        # during dog-food. Leaving client_id unset lets
        # ``queue_prompt`` fall back to ``self.client_id`` so the
        # WebSocket subscription receives the events.
        try:
            prompt_id = await self._service.submit_workflow(workflow_model)
        except (RuntimeError, ValueError) as exc:
            raise AIGatewayError(
                AIError(
                    class_=ErrorClass.BACKEND,
                    title="ExtendedComfyUIService.submit_workflow failed",
                    detail=str(exc),
                )
            ) from exc

        # Build output-node map so the handle can resolve internal_id at wait-time.
        # Use the manifest's declared outputs section as the source of truth.
        # The old prefix heuristic ``internal_id.startswith("output_")`` was
        # broken for real presets: ``_default_internal_id`` (editor/export.py)
        # slugifies the candidate's display name without adding any
        # "output_"/"slot_"/"param_" prefix, so a SaveImage candidate ends
        # up with internal_id ``"saveimage"`` — which the prefix filter
        # silently skipped. Dogfood symptom: ``output_mappings={}`` even
        # when the preset declared an output, so the generated image was
        # fetched from /history but never mapped to any internal_id.
        output_internal_ids: dict[str, str] = {}
        manifest = request.manifest if isinstance(request.manifest, dict) else {}
        declared_outputs = manifest.get("outputs")
        if isinstance(declared_outputs, list):
            for entry in declared_outputs:
                if not isinstance(entry, dict):
                    continue
                internal_id = entry.get("internal_id")
                if not isinstance(internal_id, str) or not internal_id:
                    continue
                ref = mappings.get(internal_id)
                if isinstance(ref, dict) and "node_id" in ref:
                    output_internal_ids[str(ref["node_id"])] = internal_id
        logger.info(
            "ComfyUIGateway.submit: prompt_id=%s output_mappings=%r "
            "(keys are node_ids we will look up in /history outputs)",
            prompt_id,
            output_internal_ids,
        )

        return _ServiceBackedJobHandle(
            service=self._service,
            request=request,
            prompt_id=prompt_id,
            output_node_ids=list(output_internal_ids.keys()),
            output_internal_ids=output_internal_ids,
        )

    async def dispose(self) -> None:
        self._disposed = True

    async def _use_registry_connection_for(
        self,
        target_url: str | None,
        target_server_id: str | None = None,
    ) -> None:
        """Phase 10c — replace the swap pattern with registry lookup.

        Resolves the target ComfyUI URL using the registry, picks the
        cached connection for that URL, and points the service's
        ``_connection`` at it. The registry owns connection lifecycles
        so the previous URL's connection stays alive in the cache for
        future submits — calling this method N times with N different
        URLs creates / re-uses N independent connections rather than
        destroying-and-rebuilding a single shared slot.

        Resolution order:

        1. ``target_url`` (when non-empty): the AI Effect Manager /
           preset's explicit URL. Auto-registers an ad-hoc server
           entry on first sight so the registry stays the single
           source of truth for "what URLs has this session touched?".
        2. ``target_server_id`` (Phase 10c-ui): a registered server id
           like ``"local"`` / ``"external"``. Resolves through the
           registry's id → URL map. When the id is unknown the
           registry raises ValueError, which the caller wraps in an
           AIGatewayError so the user sees a clear "unknown server"
           message rather than a silent fallback to the wrong URL.
        3. Registry's ``default_server_id``: the user-designated
           default. This is the URL ``empty endpoint_url`` should
           always resolve to — independent of whatever URL the
           previous submit ended up swapping to.

        When neither is available (no target id, no target URL and no
        default in the registry), the existing ``service._connection``
        is left in place — the same fallback the legacy swap path
        would have produced.
        """
        registry = self._registry
        if registry is None:
            return  # caller already guards on this
        if active_url(registry):
            await bind_service(self._service, registry)
            return

        connection: Any = None
        if target_url:
            connection = await registry.get_or_create_connection(target_url)
        elif target_server_id:
            connection = await registry.get_or_create_connection(target_server_id)
        else:
            default_id = registry.default_server_id
            if default_id is not None:
                connection = await registry.get_or_create_connection(default_id)

        if connection is None:
            # No registry default and no preset URL — leave the existing
            # service connection alone. Allows fresh installs (no
            # registered servers) to fall through to whatever the
            # ComfyUI panel set up at startup.
            return

        existing = getattr(self._service, "_connection", None)
        if existing is connection:
            return  # already pointing at the registry-owned connection

        # Mutate the service's slot. Unlike the legacy
        # ``_switch_service_endpoint`` we do NOT disconnect the old
        # connection here — the registry retains it for future
        # submits, so a "swap back" later is a cache hit instead of a
        # rebuild.
        self._service._connection = connection  # noqa: SLF001
        logger.info(
            "ComfyUIGateway: registry-resolved endpoint to %s (server_id=%s)",
            getattr(connection, "base_url", "<unknown>"),
            getattr(registry.get_by_url(getattr(connection, "base_url", "")), "id", None),
        )

    async def _switch_service_endpoint(self, endpoint_url: str) -> None:
        """Swap the service's connection to a different ComfyUI URL.

        Called by :meth:`submit` when the preset's gateway_file declares
        an ``endpoint_url`` that differs from the current connection's
        ``base_url``. The old connection is disconnected (best-effort)
        and a fresh :class:`AsyncComfyUIConnection` bound to the new URL
        replaces it. On the next ``connect()`` call (issued in the
        same submit path) the service uses the new URL.

        Idempotent: if the service already has a connection for
        ``endpoint_url`` this is a no-op. Failures during the disconnect
        step are logged at debug level — the disconnect is courtesy
        only; the swap itself is what matters.
        """
        existing = getattr(self._service, "_connection", None)
        existing_url = getattr(existing, "base_url", None) if existing is not None else None
        if existing_url == endpoint_url:
            return

        from loomstroke.platform import get_async_manager  # noqa: PLC0415

        from ...comfyui.async_connection import (  # noqa: PLC0415
            AsyncComfyUIConnection as ComfyUIConnection,
        )

        if existing is not None:
            try:
                disconnect_fn = getattr(existing, "disconnect", None)
                if callable(disconnect_fn):
                    await disconnect_fn()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Pre-switch disconnect failed (ignored): %s", exc, exc_info=True)

        async_manager = get_async_manager()
        new_connection = ComfyUIConnection(async_manager=async_manager, base_url=endpoint_url)
        # Mutate the service's connection slot directly. The ComfyUI
        # panel uses the same pattern (see ``ui/connection_bar.py``
        # ``self.service._connection = None`` for force-reconnect),
        # so this is the established workaround for the lack of a
        # dedicated "switch URL" service method.
        self._service._connection = new_connection  # noqa: SLF001
        logger.info("ComfyUIGateway: switched service endpoint to %s", endpoint_url)


# =====================================================================
# Internal helpers
# =====================================================================


def _extract_available_node_names(object_info: Any) -> set[str]:
    """Accept both ExtendedComfyUIService's wrapped shape and the
    legacy direct-node-dict shape returned by earlier connections.

    - Wrapped: ``{"nodes": {node_type: ...}, "models": {...}}`` →
      take the keys of ``object_info["nodes"]``.
    - Direct: ``{node_type: schema, ...}`` → take the keys directly.

    Returns an empty set for malformed inputs so
    :meth:`ComfyUIGateway.check_dependencies` flags genuine missing
    nodes rather than crashing on a shape surprise.
    """
    if not isinstance(object_info, dict):
        return set()
    # Wrapped shape: the "nodes" sub-dict is the catalogue. Presence of
    # both "nodes" and "models" keys is the ExtendedComfyUIService
    # fingerprint; guard on dict-ness to tolerate older stubs.
    nodes_entry = object_info.get("nodes")
    if isinstance(nodes_entry, dict):
        return set(nodes_entry.keys())
    # Direct shape: every key is a node type id.
    return set(object_info.keys())


def _normalise_to_api_format(
    workflow: dict[str, Any],
    object_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert a ComfyUI workflow JSON to API format.

    Reuses :class:`loomstroke.plugins.comfyui.workflow_model.WorkflowModel`
    which already handles both the UI-format (``{"nodes": [...]}``) and
    API-format (``{node_id: {class_type, inputs, ...}}``) layouts. An
    already-API-format input round-trips unchanged.

    ``object_info`` (when available) lets ``to_api_format`` map
    ``widgets_values`` through the node's actual schema, so custom nodes
    outside the static widget table keep their widget values.
    """
    from loomstroke.plugins.comfyui.workflow_model import WorkflowModel  # noqa: PLC0415

    model = WorkflowModel.from_json(workflow)
    return model.to_api_format(object_info)


_MISSING = object()

_COMFYUI_CONNECTION_INPUT_TYPES = {
    "LATENT",
    "CONDITIONING",
    "MODEL",
    "VAE",
    "CLIP",
    "CONTROL_NET",
    "IMAGE",
    "MASK",
}


def _cached_object_info_for_request(
    service: Any,
    registry: Any,
    gateway_file: dict[str, Any],
) -> dict[str, Any] | None:
    target_url = _clean_str(gateway_file.get("endpoint_url"))
    target_server_id = _clean_str(gateway_file.get("target_server_id"))

    if registry is not None:
        cache_key = (
            active_url(registry)
            or target_url
            or target_server_id
            or _clean_str(getattr(registry, "default_server_id", None))
        )
        if cache_key:
            getter = getattr(registry, "get_object_info_cache", None)
            cached = getter(cache_key) if callable(getter) else None
            if _has_object_info_nodes(cached):
                return cached
            resolved_url = (
                active_url(registry) or target_url or _registry_url_for(registry, cache_key)
            )
            current_url = _connection_url(service)
            if resolved_url and current_url and resolved_url.rstrip("/") != current_url.rstrip("/"):
                return None

    cache = getattr(service, "_object_info_cache", None)
    nodes = getattr(cache, "nodes", None) if cache is not None else None
    if isinstance(nodes, dict) and nodes:
        models = getattr(cache, "models", {}) if cache is not None else {}
        return {"nodes": nodes, "models": models if isinstance(models, dict) else {}}
    return None


def _clean_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _has_object_info_nodes(value: Any) -> bool:
    nodes = value.get("nodes") if isinstance(value, dict) else None
    return isinstance(nodes, dict) and bool(nodes)


def _registry_url_for(registry: Any, server_id_or_url: str) -> str | None:
    getter = getattr(registry, "get", None)
    target = getter(server_id_or_url) if callable(getter) else None
    raw_url = getattr(target, "url", None)
    return raw_url.strip() if isinstance(raw_url, str) and raw_url.strip() else None


def _connection_url(service: Any) -> str | None:
    connection = getattr(service, "_connection", None)
    raw_url = getattr(connection, "base_url", None)
    return raw_url.strip() if isinstance(raw_url, str) and raw_url.strip() else None


def _fill_missing_required_input_defaults(
    workflow: dict[str, Any],
    object_info: dict[str, Any] | None,
) -> list[tuple[str, str, Any]]:
    """Backfill required scalar inputs whose ComfyUI schema declares a default.

    Presets exported from a UI-format workflow can lack custom-node widget
    values when those widgets were not surfaced as AI Effect parameters.
    We only fill schema-declared defaults and deliberately skip graph-wire
    inputs such as IMAGE / LATENT / MODEL so missing real connections still
    fail validation instead of being hidden.
    """
    nodes = _object_info_nodes(object_info)
    if not nodes:
        return []

    filled: list[tuple[str, str, Any]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if not isinstance(class_type, str) or not class_type:
            continue
        schema = nodes.get(class_type)
        if not isinstance(schema, dict):
            continue
        required = schema.get("input", {}).get("required", {})
        if not isinstance(required, dict):
            continue
        inputs = node.setdefault("inputs", {})
        if not isinstance(inputs, dict):
            continue
        for input_key, input_spec in required.items():
            if not isinstance(input_key, str) or input_key in inputs:
                continue
            default = _default_from_input_spec(input_spec)
            if default is _MISSING:
                continue
            value = copy.deepcopy(default)
            inputs[input_key] = value
            filled.append((str(node_id), input_key, value))
    return filled


def _object_info_nodes(object_info: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(object_info, dict):
        return {}
    nodes = object_info.get("nodes") if "nodes" in object_info else object_info
    return nodes if isinstance(nodes, dict) else {}


def _default_from_input_spec(input_spec: Any) -> Any:
    if not isinstance(input_spec, (list, tuple)) or not input_spec:
        return _MISSING
    head = input_spec[0]
    options_raw = input_spec[1] if len(input_spec) > 1 else {}
    options = options_raw if isinstance(options_raw, dict) else {}

    if isinstance(head, str) and head.upper() in _COMFYUI_CONNECTION_INPUT_TYPES:
        return _MISSING
    if "default" in options:
        return options["default"]
    return _MISSING


def _snapshot_slot_png_for_diagnosis(internal_id: str, png_bytes: bytes) -> None:
    """Write the slot's PNG to ``user_data/logs/ai_effect_slots/`` for triage.

    Each generation overwrites ``slot_<internal_id>.png``. Lets dogfood
    open the file and visually confirm what LoomStroke actually shipped
    to ComfyUI — the only way to distinguish "the upload was correct
    but ComfyUI ignored it" from "we shipped a near-blank image" without
    standing up a debugger.

    Failures are swallowed: a missing user_data root or a read-only
    disk must not block generation. This is diagnostic-only.
    """
    try:
        from loomstroke.platform.user_dirs import default_user_root  # noqa: PLC0415

        slots_dir = default_user_root() / "logs" / "ai_effect_slots"
        slots_dir.mkdir(parents=True, exist_ok=True)
        # Sanitise the internal_id to filesystem-safe — same rule as
        # the editor's filename sanitiser, but inlined to avoid a
        # cross-package import for a one-line diagnostic.
        safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in internal_id) or "slot"
        out_path = slots_dir / f"slot_{safe_id}.png"
        out_path.write_bytes(png_bytes)
        logger.info(
            "ComfyUIGateway.submit: slot %s payload mirrored to %s "
            "(open this file to verify the content LoomStroke sent to ComfyUI)",
            internal_id,
            out_path,
        )
    except Exception:  # noqa: BLE001 - diagnostic only, never fatal
        logger.debug("Slot snapshot write failed for %s", internal_id, exc_info=True)


def _resolve_output_candidate_ref(
    node_id: str,
    node: dict[str, Any],
    workflow: dict[str, Any],
) -> tuple[str, str] | None:
    """Return ``(history_node_id, source_hint)`` for authorable output nodes.

    ``Preview3D`` nodes are display/inspection nodes in many ComfyUI
    workflows. Pixal3D wires them to an upstream GLB exporter, and that
    exporter is the node ComfyUI history is expected to report under
    ``outputs``. Keep the preview node's friendly display name while mapping
    the saved gateway ref to the exporter node.
    """
    class_type = str(node.get("class_type", ""))
    # These D2 nodes return standard history `images`, including Eagle saves.
    if class_type in ("PreviewImage", "SaveImage", "D2 Save Image", "D2 Save Image Eagle"):
        return node_id, f"Node#{node_id} {class_type}"
    if _is_preview_3d_node_type(class_type):
        inputs = node.get("inputs")
        linked_id = _linked_node_id(inputs.get("model_file")) if isinstance(inputs, dict) else None
        linked_node = workflow.get(linked_id) if linked_id else None
        if isinstance(linked_node, dict):
            linked_class = str(linked_node.get("class_type", ""))
            if _is_3d_artifact_output_node_type(linked_class):
                return (
                    linked_id,
                    f"Node#{node_id} {class_type} -> Node#{linked_id} {linked_class}",
                )
        return node_id, f"Node#{node_id} {class_type}"
    if _is_3d_artifact_output_node_type(class_type):
        return node_id, f"Node#{node_id} {class_type}"
    return None


def _linked_node_id(value: Any) -> str | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    head = value[0]
    if isinstance(head, (str, int)):
        return str(head)
    return None


def _is_preview_3d_node_type(node_type: str) -> bool:
    normalized = _normalise_node_type_for_match(node_type)
    return normalized in {"preview3d", "preview3danimation"}


def _is_3d_artifact_output_node_type(node_type: str) -> bool:
    normalized = _normalise_node_type_for_match(node_type)
    if normalized in {
        "save3d",
        "save3dmodel",
        "save3dmesh",
        "saveglb",
        "savegltf",
        "saveobj",
        "savemesh",
        "exportglb",
        "exportgltf",
        "exportobj",
        "pixal3dexportglb",
    }:
        return True
    if "save" not in normalized and "export" not in normalized:
        return False
    return any(token in normalized for token in ("3d", "mesh", "glb", "gltf", "obj", "model"))


def _normalise_node_type_for_match(node_type: str) -> str:
    return "".join(ch for ch in str(node_type or "").lower() if ch.isalnum())


def _constraints_from_object_info(  # noqa: PLR0911, PLR0912
    object_info: dict[str, Any] | None,
    class_type: str,
    input_key: str,
) -> tuple[str | None, dict[str, Any] | None, list[Any] | None, bool]:
    """Pull (type, range, choices, multiline) for ``class_type.input_key``.

    Tolerates the two ``object_info`` shapes :class:`ExtendedComfyUIService`
    has produced over time:

    - **Wrapped** ``{"nodes": {class_type: schema, ...}, "models": {...}}``
      — current shape.
    - **Direct** ``{class_type: schema, ...}`` — older test stubs and
      raw responses from earlier connection layers.

    For each ``inputs.required[key]`` (and ``inputs.optional[key]``)
    spec, ComfyUI uses one of three layouts:

    - ``[primitive_type_name, options_dict]`` — e.g. ``["INT", {"min":..., "max":...}]``
    - ``[[choice1, choice2, ...], options_dict]`` — combo input
    - ``[connection_type_name]`` — graph wire, not a parameter

    Returns ``(None, None, None, False)`` for any miss so the caller
    falls back to value-shape inference cleanly.
    """
    if not isinstance(object_info, dict):
        return None, None, None, False
    nodes = object_info.get("nodes") if "nodes" in object_info else object_info
    if not isinstance(nodes, dict):
        return None, None, None, False
    schema = nodes.get(class_type)
    if not isinstance(schema, dict):
        return None, None, None, False
    inputs = schema.get("input")
    if not isinstance(inputs, dict):
        return None, None, None, False
    spec: Any = None
    for section in ("required", "optional"):
        section_data = inputs.get(section)
        if isinstance(section_data, dict) and input_key in section_data:
            spec = section_data[input_key]
            break
    if not isinstance(spec, (list, tuple)) or not spec:
        return None, None, None, False
    head = spec[0]
    options_raw = spec[1] if len(spec) > 1 else {}
    options = options_raw if isinstance(options_raw, dict) else {}

    # Combo / enum input — legacy (head is the choice list) or current
    # (head is "COMBO" and the choices live in options["options"]).
    if is_combo_head(head):
        choices = combo_choices(head, options)
        if not choices:
            return None, None, None, False
        return "enum", None, choices, False

    if not isinstance(head, str):
        return None, None, None, False

    head_upper = head.upper()
    if head_upper == "INT":
        return "int", _options_to_range(options), None, False
    if head_upper == "FLOAT":
        return "float", _options_to_range(options), None, False
    if head_upper == "STRING":
        multiline = bool(options.get("multiline", False))
        return "string", None, None, multiline
    if head_upper == "BOOLEAN":
        return "bool", None, None, False
    # Graph wire types ("MODEL", "CONDITIONING", "LATENT", ...) — these
    # never carry a scalar value so the caller's ``_is_exposable_scalar``
    # filter has already excluded them; we only reach here if a custom
    # node uses an unknown primitive name.
    return None, None, None, False


def _options_to_range(options: dict[str, Any]) -> dict[str, Any] | None:
    """Project ComfyUI options dict to LoomStroke's range schema."""
    rng: dict[str, Any] = {}
    for src, dst in (("min", "min"), ("max", "max"), ("step", "step")):
        if src in options:
            rng[dst] = options[src]
    return rng or None


def _is_exposable_scalar(value: Any) -> bool:
    # bool is a subclass of int but we treat it as an exposable scalar in its
    # own right — separate branch makes the intent clear.
    if isinstance(value, bool):
        return True
    return isinstance(value, (int, float, str))


def _scalar_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "string"


def _derive_slot_name(node: dict[str, Any]) -> str:
    meta = node.get("_meta")
    if isinstance(meta, dict):
        title = meta.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return "Input"


def _derive_output_name(node: dict[str, Any]) -> str:
    meta = node.get("_meta")
    if isinstance(meta, dict):
        title = meta.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return str(node.get("class_type", "Output"))


def _extract_dependencies(workflow: dict[str, Any]) -> dict[str, Any]:
    """Collect referenced class_types so dependency checks can flag missing."""
    custom_nodes: set[str] = set()
    for node in workflow.values():
        if isinstance(node, dict):
            ct = node.get("class_type")
            if isinstance(ct, str) and ct:
                custom_nodes.add(ct)
    return {"custom_nodes": sorted(custom_nodes)}


def _extract_outputs(
    *,
    history: dict[str, Any],
    job_id: str,
    output_node_ids: list[str],
    output_internal_ids: dict[str, str],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Pick ``{filename, subfolder, type}`` triples out of the history record.

    Returns a list of ``(internal_id, list_of_file_refs)`` pairs preserving
    the author-declared order.

    Accepts two shapes for ``history`` because the upstream path is
    inconsistent:

    - **Wrapped** ``{prompt_id: record}`` — the raw response from
      ComfyUI's ``/history/{prompt_id}`` endpoint.
    - **Unwrapped** ``record`` — what
      :meth:`AsyncComfyUIConnection.get_result` actually returns
      (``history[prompt_id]``), which is what
      :meth:`ExtendedComfyUIService._stream_job` stores in
      ``JobResultEvent.result_data`` *and* what our own
      ``_poll_history_until_ready`` falls back to. Historically the
      extractor only handled the wrapped shape; production was sending
      unwrapped, so every output-lookup silently returned empty.
    """
    if not isinstance(history, dict):
        return []
    outputs = _history_outputs_dict(history=history, job_id=job_id)
    if not outputs:
        return []
    result: list[tuple[str, list[dict[str, Any]]]] = []
    for node_id in output_node_ids:
        node_out = outputs.get(node_id)
        if not isinstance(node_out, dict):
            continue
        images = node_out.get("images")
        if not isinstance(images, list):
            continue
        internal_id = output_internal_ids.get(node_id, node_id)
        file_refs = [
            img for img in images if isinstance(img, dict) and isinstance(img.get("filename"), str)
        ]
        if file_refs:
            result.append((internal_id, file_refs))
    return result


_MODEL_FILE_SUFFIXES = {".glb", ".gltf", ".obj"}
_MODEL_OUTPUT_KEYS = {
    "3d",
    "3d_model",
    "3d_models",
    "artifact",
    "artifacts",
    "file",
    "files",
    "glb",
    "gltf",
    "mesh",
    "meshes",
    "model",
    "model_file",
    "model_path",
    "models",
    "obj",
    "output",
    "outputs",
    "result",
    "text",
}
_COMFY_OUTPUT_FOLDERS = {"input", "output", "temp"}


def _history_outputs_dict(*, history: dict[str, Any] | None, job_id: str) -> dict[str, Any]:
    """Return the ``outputs`` dict from wrapped or unwrapped ComfyUI history."""
    if not isinstance(history, dict):
        return {}
    # Detect unwrapped: a record dict has "outputs"/"status"/"prompt" at
    # the top level. A wrapped dict has the prompt_id as a top-level key.
    if "outputs" in history or "status" in history or "prompt" in history:
        record: dict[str, Any] = history
    else:
        maybe_record = history.get(job_id)
        record = maybe_record if isinstance(maybe_record, dict) else {}
    outputs = record.get("outputs") if isinstance(record, dict) else None
    return outputs if isinstance(outputs, dict) else {}


def _extract_model_outputs(
    *,
    history: dict[str, Any],
    job_id: str,
    output_node_ids: list[str],
    output_internal_ids: dict[str, str],
) -> list[OutputModel]:
    outputs = _history_outputs_dict(history=history, job_id=job_id)
    if not outputs:
        return []
    result: list[OutputModel] = []
    for node_id in output_node_ids:
        entries: list[Any] = []
        _collect_model_entries(outputs.get(node_id), entries, key_hint=None)
        if not entries:
            continue
        internal_id = output_internal_ids.get(node_id, node_id)
        for entry in entries:
            file_info = _model_file_info(entry)
            if file_info is None:
                continue
            filename, subfolder, folder_type = file_info
            result.append(
                OutputModel(
                    internal_id=internal_id,
                    node_id=node_id,
                    filename=filename,
                    subfolder=subfolder,
                    folder_type=folder_type,
                )
            )
    return result


def _collect_model_entries(value: Any, entries: list[Any], *, key_hint: str | None) -> None:
    if value is None:
        return
    if isinstance(value, dict):
        filename = _entry_filename(value)
        artifact_kind = str(
            value.get("kind") or value.get("role") or value.get("artifact_type") or ""
        ).lower()
        if filename and (
            _is_model_output_hint(key_hint)
            or _is_model_path(filename)
            or artifact_kind in {"mesh", "model", "3d", "glb", "gltf", "obj"}
        ):
            entries.append(value)
            return
        for key, child in value.items():
            _collect_model_entries(child, entries, key_hint=str(key).lower())
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _collect_model_entries(child, entries, key_hint=key_hint)
        return
    if isinstance(value, str) and (_is_model_output_hint(key_hint) or _is_model_path(value)):
        entries.append(value)


def _model_file_info(entry: Any) -> tuple[str, str, str] | None:
    filename: str | None = None
    subfolder = ""
    folder_type = "output"
    if isinstance(entry, dict):
        filename = _entry_filename(entry)
        subfolder = str(entry.get("subfolder") or "")
        raw_folder = entry.get("folder_type") or entry.get("type") or "output"
        raw_folder_text = str(raw_folder or "output").lower()
        if raw_folder_text in _COMFY_OUTPUT_FOLDERS:
            folder_type = raw_folder_text
    elif isinstance(entry, str):
        filename = entry
    if not filename:
        return None
    filename, subfolder = _split_comfy_output_path(filename, subfolder)
    if not _is_model_path(filename):
        return None
    return filename, subfolder, folder_type


def _entry_filename(entry: dict[str, Any]) -> str | None:
    for key in ("filename", "name", "file", "path", "local_path", "model_file", "model_path"):
        value = entry.get(key)
        if value:
            return str(value)
    return None


def _is_model_output_hint(key_hint: str | None) -> bool:
    if not key_hint:
        return False
    normalized = "".join(ch if ch.isalnum() else "_" for ch in key_hint.lower()).strip("_")
    return normalized in _MODEL_OUTPUT_KEYS or any(
        token in normalized for token in ("3d", "model", "mesh", "glb", "gltf", "obj")
    )


def _is_model_path(value: str) -> bool:
    return Path(str(value).split("?", 1)[0]).suffix.lower() in _MODEL_FILE_SUFFIXES


def _split_comfy_output_path(filename: str, subfolder: str) -> tuple[str, str]:
    raw = str(filename).replace("\\", "/")
    parts = [part for part in raw.split("/") if part]
    if not parts:
        return filename, subfolder
    basename = parts[-1]
    if subfolder or raw.startswith("/") or (parts and parts[0].endswith(":")):
        return basename, subfolder
    if len(parts) > 1:
        return basename, "/".join(parts[:-1])
    return basename, subfolder


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _parse_png_dims(png_bytes: bytes) -> tuple[int, int]:
    """Return ``(width, height)`` parsed from the PNG IHDR chunk.

    The PNG spec fixes IHDR as the first chunk after the signature. Bytes
    16..24 are ``(width, height)`` as big-endian ``uint32``. Falls back to
    ``(0, 0)`` for anything that isn't a valid PNG header.
    """
    if len(png_bytes) < 24 or not png_bytes.startswith(_PNG_SIGNATURE):
        return (0, 0)
    try:
        width, height = struct.unpack(">II", png_bytes[16:24])
    except struct.error:
        return (0, 0)
    return int(width), int(height)
