"""Full AI Effect generation flow (Phase 4-3).

Spec reference:
- ``AIeffect_design/04_python_design/gateway_protocol.md`` §4.2 (generation flow)
- ``AIeffect_design/04_python_design/error_handling.md`` §4 (auto retry)
- ``AIeffect_design/04_python_design/gallery_integration.md`` §3 / §5

Wiring:

.. code-block:: text

    AIEffectGenerationController.execute()
        ├── validate_inputs()                   VALIDATION error → stop
        ├── compute_param_hash()                (pre-resolve hash)
        ├── gallery.get_by_param_hash()         cache hit → return LastResult
        ├── gateway.check_dependencies()        DEPENDENCY error → stop
        ├── gateway.submit()                    → JobHandle
        ├── async for event in handle.progress():
        │     state machine: queued/generating/done
        ├── handle.wait()                       → JobResult
        ├── recompute param_hash (seed -1 → resolved)
        ├── gallery.append(record)
        └── return GenerationOutcome.success

Retry policy (error_handling.md §4.1):
- ``backend`` / ``timeout`` errors retry exactly once after
  ``retry_delay_ms`` when the current trigger is not ``"manual"``
- ``manual`` trigger does NOT auto-retry (respects user intent)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from loomstroke.plugins.ai_effect.controller import (
    AIEffectController,
    LayerState,
    can_transition,
)
from loomstroke.plugins.ai_effect.data_model import (
    AIEffectState,
    GenerationState,
    LastResult,
    SlotRef,
)
from loomstroke.plugins.ai_effect.errors import AIError, AIGatewayError, ErrorClass
from loomstroke.plugins.ai_effect.gallery_bridge import (
    build_generation_record,
    matches_param_hash,
)
from loomstroke.plugins.ai_effect.gateway_protocol import (
    AIGateway,
    ExecutionRequest,
    JobHandle,
    JobResult,
    ProgressEvent,
    SlotImage,
)
from loomstroke.plugins.ai_effect.param_hash import compute_param_hash
from loomstroke.plugins.ai_effect.settings import AIEffectPreferences
from loomstroke.plugins.ai_effect.telemetry import AIEffectTelemetry

if TYPE_CHECKING:
    from loomstroke.core.generation_record import GenerationRecord

logger = logging.getLogger(__name__)


# ======================================================================
# Gallery integration point — abstract so tests can supply an in-memory store
# ======================================================================


class GalleryStore(Protocol):
    """Minimal surface over the host's GenerationHistoryStore.

    Real implementation (not in this plugin) writes to disk. Tests pass an
    in-memory fake. ``get_by_param_hash`` is allowed to be slow the first
    time (linear scan) — Phase 9 may add an index.
    """

    def get_by_param_hash(self, param_hash: str) -> GenerationRecord | None:
        """Return the most recent record whose ai_effect.param_hash matches, or None."""
        ...

    def append(
        self,
        record: GenerationRecord,
        images: list[bytes],
    ) -> None:
        """Persist one completed generation. ``images`` is the raw PNG bytes
        for each ``record.output_images`` entry, in index order."""
        ...


# ======================================================================
# Outcome dataclass
# ======================================================================


@dataclass
class GenerationOutcome:
    """Terminal result of ``AIEffectGenerationController.execute``.

    Exactly one of ``status ∈ {"success", "cache_hit", "cancelled", "error"}``
    is set. Consumers should branch on ``status`` and then use the matching
    fields.
    """

    status: str  # "success" / "cache_hit" / "cancelled" / "error"
    layer_id: str = ""
    param_hash: str = ""
    error: AIError | None = None
    #: Set on ``success``: the freshly produced JobResult.
    result: JobResult | None = None
    #: Set on ``success``: the GenerationRecord persisted to Gallery.
    record: GenerationRecord | None = None
    #: Set on ``cache_hit``: the previously-persisted GenerationRecord.
    cached_record: GenerationRecord | None = None
    #: Number of auto-retries attempted during this ``execute`` call (0-2).
    retry_count: int = 0
    #: Progress events observed while streaming (for tests / logging).
    observed_progress: list[ProgressEvent] = field(default_factory=list)


# ======================================================================
# Controller
# ======================================================================


class AIEffectGenerationController:
    """Drives one full generation (validate → submit → Gallery → state).

    Thread safety: must run on a qasync event loop (Qt main thread). Not
    safe to call ``execute`` concurrently for the same ``layer_id`` —
    caller must serialize per-layer generations. UI-level enforcement
    lives in gateway_protocol.md §5.2.
    """

    def __init__(
        self,
        *,
        state_machine: AIEffectController,
        preferences: AIEffectPreferences,
        gallery: GalleryStore | None = None,
        telemetry: AIEffectTelemetry | None = None,
        on_preview: Callable[[str, bytes], None] | None = None,
    ) -> None:
        self._state_machine = state_machine
        self._prefs = preferences
        self._gallery = gallery
        # Phase 8c-4: optional JSON-line telemetry sink. None disables;
        # passing an instance whose ``enabled`` is False also disables
        # at zero overhead. Wired by app.py via
        # ``AIEffectTelemetry.from_preferences``.
        self._telemetry = telemetry
        # Phase 8c-2 follow-up: optional preview-image callback. Called
        # with ``(layer_id, png_bytes)`` whenever a progress event
        # carrying a non-empty ``preview_png`` passes the throttle
        # gate. The latest-only coalescing inside
        # ``_dispatch_progress_event`` guarantees the callback sees
        # at most one preview per ``ui.preview_throttle_ms`` window —
        # a UI consumer can therefore drive a 2fps preview thumbnail
        # without backpressure logic of its own. None disables.
        self._on_preview = on_preview
        # Per-layer asyncio.Lock to serialize concurrent execute() calls on
        # the same layer_id (code review #6). Qt "main thread only" is not
        # a guard once ``await`` introduces task-switch points.
        self._locks: dict[str, asyncio.Lock] = {}
        # Phase 8c-1: per-gateway asyncio.Semaphore caps concurrent in-flight
        # generations against a backend (settings.md §2.1.1 — local ComfyUI
        # is serial, API gateways permit 2 by default). Acquisition order is
        # fixed Semaphore → Lock (deadlock proof: lock graph is a single
        # chain). Limit is read at first acquire; preference edits during a
        # session do NOT resize the semaphore (restart required).
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        # Phase 8c-2: progress-event throttle clock. Default is the
        # monotonic wall clock; tests inject a deterministic clock so
        # they don't depend on real time. Public API stays stable —
        # production code never sets this.
        self._progress_clock: Callable[[], float] = time.monotonic
        # Phase 8c-3: track in-flight execute() Tasks per layer so
        # external teardown (layer deletion, preset swap, page change)
        # can cancel them via ``cancel_layer``. Multiple parallel
        # execute() calls on one layer all register; ``cancel_layer``
        # cancels every still-running task and returns whether any
        # were live.
        self._inflight_tasks: dict[str, list[asyncio.Task[GenerationOutcome]]] = {}

    def _lock_for(self, layer_id: str) -> asyncio.Lock:
        lock = self._locks.get(layer_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[layer_id] = lock
        return lock

    def _semaphore_for(self, gateway_id: str) -> asyncio.Semaphore:
        sem = self._semaphores.get(gateway_id)
        if sem is None:
            limit = max(1, int(self._prefs.concurrency.get(gateway_id, default=1)))
            sem = asyncio.Semaphore(limit)
            self._semaphores[gateway_id] = sem
        return sem

    def cancel_layer(self, layer_id: str) -> bool:
        """Cancel any in-flight ``execute`` Task(s) for ``layer_id``.

        Used for external teardown (layer deletion, preset swap, page
        change) so a stale generation does not write its outcome back
        to the canvas after the layer the user cared about is gone.
        Returns True iff at least one not-yet-done Task was cancelled.

        Cancellation propagates the standard way: the awaiting caller
        sees ``asyncio.CancelledError``. The per-gateway Semaphore and
        per-layer Lock are released by the ``async with`` machinery on
        the way out.
        """
        tasks = self._inflight_tasks.get(layer_id)
        if not tasks:
            return False
        cancelled_any = False
        for task in tasks:
            if not task.done():
                task.cancel()
                cancelled_any = True
        return cancelled_any

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        *,
        layer_id: str,
        state: AIEffectState,
        gateway: AIGateway,
        layer_versions: dict[str, int],
        slot_images: dict[str, SlotImage],
        source_page_id: str | None = None,
        force_regenerate: bool = False,
    ) -> GenerationOutcome:
        """Run one end-to-end generation for ``layer_id``.

        Returns a GenerationOutcome describing the terminal outcome. The
        state machine (``AIEffectController``) has been advanced
        appropriately by the time this returns. Never raises — errors are
        carried in ``GenerationOutcome``.

        Concurrent calls with the same ``layer_id`` are serialized by a
        per-layer ``asyncio.Lock`` (code review #6). Concurrent calls
        targeting the same gateway are gated by a per-gateway
        ``asyncio.Semaphore`` whose size is ``preferences.concurrency.
        {gateway_id}`` (Phase 8c-1). Acquisition order is fixed
        Semaphore → Lock to keep the lock graph acyclic.

        While a call is waiting on the semaphore the layer is dispatched
        to ``QUEUED`` so the UI can surface the queue position. The
        dispatch is idempotent — if the layer is already QUEUED or
        GENERATING the transition is skipped.
        """
        gateway_id = str(getattr(gateway, "gateway_id", "") or "")
        # Pre-dispatch QUEUED so the UI shows the layer as queued during
        # the semaphore wait. ``can_transition`` makes this a no-op when
        # the layer is already QUEUED / GENERATING (idempotent).
        if can_transition(self._state_machine.state_of(layer_id), "generate"):
            self._state_machine.dispatch(layer_id, "generate", state=state)

        # Phase 8c-3: register this Task so ``cancel_layer`` can abort
        # it on external teardown. Multiple parallel callers each
        # register and unregister independently.
        current_task: asyncio.Task[GenerationOutcome] | None = (
            asyncio.current_task()  # type: ignore[assignment]
        )
        tracked_list = self._inflight_tasks.setdefault(layer_id, [])
        if current_task is not None:
            tracked_list.append(current_task)

        # Phase 8c-4: telemetry — record start time so ``log_generation``
        # can report elapsed_ms regardless of which terminal branch we
        # take (success / cache_hit / cancelled / external cancel).
        start_t = self._progress_clock()
        outcome: GenerationOutcome | None = None
        try:
            semaphore = self._semaphore_for(gateway_id) if gateway_id else None
            if semaphore is None:
                async with self._lock_for(layer_id):
                    outcome = await self._execute_locked(
                        layer_id=layer_id,
                        state=state,
                        gateway=gateway,
                        layer_versions=layer_versions,
                        slot_images=slot_images,
                        source_page_id=source_page_id,
                        force_regenerate=force_regenerate,
                    )
            else:
                async with semaphore, self._lock_for(layer_id):
                    outcome = await self._execute_locked(
                        layer_id=layer_id,
                        state=state,
                        gateway=gateway,
                        layer_versions=layer_versions,
                        slot_images=slot_images,
                        source_page_id=source_page_id,
                        force_regenerate=force_regenerate,
                    )
            return outcome
        finally:
            if current_task is not None:
                with contextlib.suppress(ValueError):
                    tracked_list.remove(current_task)
            if not tracked_list:
                self._inflight_tasks.pop(layer_id, None)
            if self._telemetry is not None:
                elapsed_ms = (self._progress_clock() - start_t) * 1000.0
                if outcome is not None:
                    phase = outcome.status
                    cache_hit = outcome.status == "cache_hit"
                    retry_count = outcome.retry_count
                else:
                    # Outer Task cancelled before _execute_locked
                    # returned (e.g. cancel_layer fired during
                    # gateway.submit). Distinguish from gateway-emitted
                    # ``cancelled`` so the log line is greppable.
                    phase = "cancelled_external"
                    cache_hit = False
                    retry_count = 0
                self._telemetry.log_generation(
                    phase=phase,
                    layer_id=layer_id,
                    elapsed_ms=elapsed_ms,
                    cache_hit=cache_hit,
                    gateway_id=gateway_id,
                    retry_count=retry_count,
                )

    async def _execute_locked(
        self,
        *,
        layer_id: str,
        state: AIEffectState,
        gateway: AIGateway,
        layer_versions: dict[str, int],
        slot_images: dict[str, SlotImage],
        source_page_id: str | None,
        force_regenerate: bool,
    ) -> GenerationOutcome:
        # --- Validate inputs first (VALIDATION errors never touch Gateway) ---
        validation_err = self._validate_inputs(state)
        if validation_err is not None:
            # Use force_error to handle the "already-in-ERROR" case safely
            # (code review #2 — previous code could raise InvalidTransitionError
            # when the state machine was ERROR from a prior failure).
            self._state_machine.force_error(layer_id, validation_err, state=state)
            return GenerationOutcome(status="error", layer_id=layer_id, error=validation_err)

        # --- Compute pre-resolve param_hash (wrap KeyError: code review #5) ---
        try:
            pre_hash = compute_param_hash(
                preset_id=state.preset_id,
                gateway_file_hash=state.gateway_file_hash,
                param_values=state.param_values,
                slot_refs=state.slot_refs,
                layer_versions=layer_versions,
            )
        except KeyError as exc:
            missing_layer_id = str(exc.args[0]) if exc.args else "<unknown>"
            err = AIError(
                class_=ErrorClass.VALIDATION,
                title="Referenced layer version missing",
                detail=f"layer_id={missing_layer_id}",
                raw={"missing_layer_id": missing_layer_id, "issue": "layer_versions_missing"},
            )
            self._state_machine.force_error(layer_id, err, state=state)
            return GenerationOutcome(status="error", layer_id=layer_id, error=err)

        # --- Gallery cache lookup (skipped on forced regenerate) ---
        if (
            not force_regenerate
            and not self._prefs.cache.disable_cache_for_manual_regen
            and self._gallery is not None
        ):
            cached = self._gallery.get_by_param_hash(pre_hash)
            if cached is not None and matches_param_hash(cached, pre_hash):
                logger.info("AI Effect cache hit for layer %s param_hash=%s", layer_id, pre_hash)
                # Cache hit: update last_result without touching Gateway.
                # Phase 8c-1: ``execute`` pre-dispatches QUEUED before the
                # semaphore acquire so the UI can surface the queue wait.
                # On cache hit there is no submit, so settle the layer in
                # DONE (a cached result IS a successful prior generation)
                # via force_done — QUEUED → DONE is not a defined
                # transition.
                self._apply_cache_hit_to_state(state, cached, pre_hash)
                self._state_machine.force_done(layer_id, state=state)
                return GenerationOutcome(
                    status="cache_hit",
                    layer_id=layer_id,
                    param_hash=pre_hash,
                    cached_record=cached,
                )

        # --- Advance state machine to queued (works from IDLE / DONE / ERROR) ---
        if can_transition(self._state_machine.state_of(layer_id), "generate"):
            self._state_machine.dispatch(layer_id, "generate", state=state)

        # --- Build ExecutionRequest (frozen) ---
        # preset_snapshot is keyed ``{"manifest": ..., "gateway_file": ...}``
        # per AIEffectState.from_preset (data_model.py). The old
        # ``.get("gateway", ...)`` lookup silently returned an empty
        # dict, so ComfyUIGateway.submit always tripped its
        # "missing workflow" validation guard — the real workflow
        # content was never handed to the backend.
        gateway_file_source = state.preset_snapshot.get("gateway_file", {})
        gateway_file = dict(gateway_file_source) if isinstance(gateway_file_source, dict) else {}
        state_target_server_id = getattr(state, "target_server_id", None)
        if isinstance(state_target_server_id, str) and state_target_server_id.strip():
            gateway_file["target_server_id"] = state_target_server_id.strip()
            gateway_file.pop("endpoint_url", None)
        manifest = state.preset_snapshot.get("manifest", {})
        request = ExecutionRequest(
            gateway_file=gateway_file,
            manifest=manifest if isinstance(manifest, dict) else {},
            param_values=dict(state.param_values),
            slot_images=dict(slot_images),
            param_hash=pre_hash,
            layer_id=layer_id,
            request_id=str(uuid.uuid4()),
        )

        # --- Submit + consume with retry envelope ---
        outcome = await self._submit_with_retry(
            layer_id=layer_id,
            state=state,
            gateway=gateway,
            request=request,
            layer_versions=layer_versions,
            source_page_id=source_page_id,
            pre_hash=pre_hash,
        )
        return outcome

    # ------------------------------------------------------------------
    # Internal: submit + retry
    # ------------------------------------------------------------------

    async def _submit_with_retry(
        self,
        *,
        layer_id: str,
        state: AIEffectState,
        gateway: AIGateway,
        request: ExecutionRequest,
        layer_versions: dict[str, int],
        source_page_id: str | None,
        pre_hash: str,
    ) -> GenerationOutcome:
        max_retries = (
            self._prefs.retry.max_retries_on_change
            if state.trigger != "manual"
            else self._prefs.retry.max_retries_manual
        )
        retry_delay_s = max(0.0, float(self._prefs.retry.retry_delay_ms) / 1000.0)

        attempt = 0
        while True:
            try:
                outcome = await self._run_once(
                    layer_id=layer_id,
                    state=state,
                    gateway=gateway,
                    request=request,
                    layer_versions=layer_versions,
                    source_page_id=source_page_id,
                    pre_hash=pre_hash,
                )
            except AIGatewayError as exc:
                outcome = GenerationOutcome(
                    status="error",
                    layer_id=layer_id,
                    param_hash=pre_hash,
                    error=exc.error,
                )
            except (RuntimeError, OSError, TimeoutError) as exc:
                # User report (2026-04-30): a NO_IMAGE failure surfaced
                # as an unwrapped ``RuntimeError`` from
                # ``_ServiceBackedJobHandle.wait()`` and bypassed the
                # ``AIGatewayError`` branch above. Without this catch
                # the exception propagates past ``_submit_with_retry``,
                # ``force_error`` never runs, the state machine stays
                # in GENERATING, and the layer-row Generate button
                # stays disabled until the app is restarted. Wrap into
                # a backend-class outcome so the standard final-error
                # path runs and the UI recovers.
                logger.exception(
                    "Unhandled exception in _run_once for layer %s — "
                    "wrapping into BACKEND error so state machine recovers",
                    layer_id,
                )
                outcome = GenerationOutcome(
                    status="error",
                    layer_id=layer_id,
                    param_hash=pre_hash,
                    error=AIError(
                        class_=ErrorClass.BACKEND,
                        title="Generation failed",
                        detail=str(exc),
                    ),
                )

            outcome.retry_count = attempt

            if outcome.status == "success":
                return outcome
            if outcome.status == "cancelled":
                return outcome

            # Error path — decide whether to retry
            if (
                outcome.error is not None
                and outcome.error.class_ in (ErrorClass.BACKEND, ErrorClass.TIMEOUT)
                and attempt < max_retries
            ):
                attempt += 1
                logger.info(
                    "Auto-retry %d/%d for layer %s (%s)",
                    attempt,
                    max_retries,
                    layer_id,
                    outcome.error.class_.value,
                )
                if retry_delay_s > 0:
                    await asyncio.sleep(retry_delay_s)
                # Stay in GENERATING state between retries (UI shows retry badge).
                continue

            # Final error — record and stop. Use ``force_error`` so a double
            # ERROR transition from a retry-exhausted state doesn't raise
            # InvalidTransitionError (code review #2).
            if outcome.error is not None:
                self._state_machine.force_error(layer_id, outcome.error, state=state)
            return outcome

    # ------------------------------------------------------------------
    # Internal: single attempt (submit + progress + gallery)
    # ------------------------------------------------------------------

    async def _run_once(
        self,
        *,
        layer_id: str,
        state: AIEffectState,
        gateway: AIGateway,
        request: ExecutionRequest,
        layer_versions: dict[str, int],
        source_page_id: str | None,
        pre_hash: str,
    ) -> GenerationOutcome:
        # Dependency precheck. Only catch I/O-shaped failures; let
        # AIGatewayError propagate so the retry envelope can handle it,
        # and let ``CancelledError`` / ``KeyboardInterrupt`` / ``SystemExit``
        # propagate so user cancellations are not silently swallowed
        # (code review #10).
        try:
            dep_report = await gateway.check_dependencies(request.gateway_file)
        except AIGatewayError:
            raise
        except (OSError, TimeoutError) as exc:
            logger.warning("check_dependencies I/O failure on layer %s: %s", layer_id, exc)
            dep_report = None
        if dep_report is not None and not dep_report.ok:
            names = ", ".join(dep.name for dep in dep_report.missing)
            err = AIError(
                class_=ErrorClass.DEPENDENCY,
                title="Missing dependencies",
                detail=names or "",
                raw={"missing": [dep.__dict__ for dep in dep_report.missing]},
            )
            return GenerationOutcome(
                status="error", layer_id=layer_id, param_hash=pre_hash, error=err
            )

        # Use local-time-with-offset to match the ComfyUI panel
        # (``datetime.now().astimezone()`` in
        # plugins/comfyui/ui/main_panel.py and comfyui_generation_provider).
        # The Gallery panel slices ``record.created_at[11:16]`` to display
        # HH:MM and does NOT convert UTC → local, so a UTC timestamp
        # surfaced as the user's "wall clock" off by the timezone offset
        # (e.g. JST users saw ComfyUI rows at the right time but AI
        # Effect rows 9 hours behind). Switching to ``.astimezone()``
        # makes both providers serialise the same shape.
        created_at = datetime.now().astimezone()

        # submit
        handle = await gateway.submit(request)
        # state machine: queued → generating
        if self._state_machine.state_of(layer_id) is LayerState.QUEUED:
            self._state_machine.dispatch(layer_id, "ack", state=state)
        self._update_generation_state(state, handle.job_id, created_at)

        observed, cancelled = await self._consume_progress(handle, state, layer_id)

        if cancelled:
            self._state_machine.dispatch(layer_id, "cancel", state=state)
            self._clear_generation_state(state)
            return GenerationOutcome(
                status="cancelled",
                layer_id=layer_id,
                param_hash=pre_hash,
                observed_progress=observed,
            )

        # fetch terminal result
        result = await handle.wait()
        # See created_at note above — ``astimezone()`` keeps Gallery
        # display in the user's local time without a UI-side conversion.
        completed_at = datetime.now().astimezone()

        if result.status == "cancelled":
            self._state_machine.dispatch(layer_id, "cancel", state=state)
            self._clear_generation_state(state)
            return GenerationOutcome(
                status="cancelled",
                layer_id=layer_id,
                param_hash=pre_hash,
                observed_progress=observed,
            )

        if result.status != "completed":
            err = result.error or AIError(
                class_=ErrorClass.BACKEND,
                title="Generation failed",
            )
            return GenerationOutcome(
                status="error",
                layer_id=layer_id,
                param_hash=pre_hash,
                error=err,
                observed_progress=observed,
            )

        # Recompute param_hash in case the Gateway resolved seed=-1
        post_hash = compute_param_hash(
            preset_id=state.preset_id,
            gateway_file_hash=state.gateway_file_hash,
            param_values=state.param_values,
            slot_refs=state.slot_refs,
            layer_versions=layer_versions,
        )

        record = build_generation_record(
            job_id=handle.job_id,
            gateway_id=gateway.gateway_id,
            gateway_display_name=gateway.display_name,
            state=state,
            result=result,
            param_hash=post_hash,
            layer_versions=layer_versions,
            source_layer_id=layer_id,
            source_page_id=source_page_id,
            created_at=created_at,
            completed_at=completed_at,
        )

        # Phase 8c-3: shield post-success bookkeeping so a caller-side
        # cancellation that races the wait() return cannot leave the
        # state machine half-transitioned (GENERATING) with the gallery
        # un-persisted. Shield lets CancelledError propagate to the
        # caller while the bookkeeping coroutine still completes.
        await asyncio.shield(
            self._commit_success(
                layer_id=layer_id,
                state=state,
                result=result,
                record=record,
                post_hash=post_hash,
                completed_at=completed_at,
                handle_job_id=handle.job_id,
            )
        )

        return GenerationOutcome(
            status="success",
            layer_id=layer_id,
            param_hash=post_hash,
            result=result,
            record=record,
            observed_progress=observed,
        )

    async def _commit_success(
        self,
        *,
        layer_id: str,
        state: AIEffectState,
        result: JobResult,
        record: GenerationRecord,
        post_hash: str,
        completed_at: datetime,
        handle_job_id: str,
    ) -> None:
        """Synchronous post-success bookkeeping packaged as a coroutine.

        Wrapped by ``asyncio.shield`` at the call site so cancellation
        of the outer Task does not abort gallery persistence + state
        transition halfway through.
        """
        if self._gallery is not None and self._should_register_in_gallery(state):
            images_bytes = [img.png_bytes for img in result.output_images]
            try:
                self._gallery.append(record, images_bytes)
            except Exception:
                logger.exception("Gallery append failed for job %s", handle_job_id)

        state.last_result = LastResult(
            param_hash=post_hash,
            finished_at=completed_at,
            mode=record.service_snapshot["ai_effect"].get("mode", "self"),
        )
        self._state_machine.dispatch(layer_id, "success", state=state)
        self._clear_generation_state(state)

    # ------------------------------------------------------------------
    # Internal: validation
    # ------------------------------------------------------------------

    def _should_register_in_gallery(self, state: AIEffectState) -> bool:
        """Return True when this generation should land in the Gallery.

        Phase 6-i: ``on_change`` and ``debounced`` triggers can fire
        many intermediate generations while the user drags a slider
        or types a prompt — most are throwaway probes. Skip Gallery
        registration for those by default; the
        ``gallery.register_auto_trigger_results`` preference flips
        the behaviour back on for users who want full history.

        Manual triggers always register — that's the user's explicit
        "save this result" action.
        """
        trigger = str(getattr(state, "trigger", "manual") or "manual")
        if trigger not in ("on_change", "debounced"):
            return True
        gallery_prefs = getattr(self._prefs, "gallery", None)
        register_auto = bool(getattr(gallery_prefs, "register_auto_trigger_results", False))
        return register_auto

    @staticmethod
    def _validate_inputs(state: AIEffectState) -> AIError | None:
        """Check required slots + required_text prompts. Returns None if OK."""
        manifest = state.preset_snapshot.get("manifest", {})
        if not isinstance(manifest, dict):
            return None

        # Required slots
        for slot_def in manifest.get("input_slots", []) or []:
            if not isinstance(slot_def, dict):
                continue
            if bool(slot_def.get("required")):
                internal_id = slot_def.get("internal_id")
                if not isinstance(internal_id, str):
                    continue
                ref: SlotRef | None = state.slot_refs.get(internal_id)
                if ref is None:
                    return AIError(
                        class_=ErrorClass.VALIDATION,
                        title="Required slot empty",
                        detail=f"slot={internal_id}",
                        raw={"slot_id": internal_id, "issue": "required_empty"},
                    )

        # Required text params
        for param_def in manifest.get("parameters", []) or []:
            if not isinstance(param_def, dict):
                continue
            if param_def.get("type") != "string" or not param_def.get("required_text"):
                continue
            internal_id = param_def.get("internal_id")
            if not isinstance(internal_id, str):
                continue
            val = state.param_values.get(internal_id, "")
            if not isinstance(val, str) or not val.strip():
                return AIError(
                    class_=ErrorClass.VALIDATION,
                    title="Required text empty",
                    detail=f"param={internal_id}",
                    raw={"param_id": internal_id, "issue": "required_text_empty"},
                )
        return None

    # ------------------------------------------------------------------
    # Internal: progress consumption (Phase 8c-2 throttle)
    # ------------------------------------------------------------------

    async def _consume_progress(
        self,
        handle: JobHandle,
        state: AIEffectState,
        layer_id: str,
    ) -> tuple[list[ProgressEvent], bool]:
        """Drain ``handle.progress()`` with the Phase 8c-2 throttle.

        Returns ``(observed, cancelled)``. ``observed`` contains every
        event yielded by the gateway (full stream — never throttled).
        Intermediate ``progress`` events are coalesced inside the
        ``ui.preview_throttle_ms`` window so ``_apply_progress_to_state``
        is called at most once per window. ``started`` / ``done`` /
        ``error`` always bypass the throttle and flush any pending
        in-window event first so its ``preview_png`` is not lost.

        ``layer_id`` is forwarded to ``_dispatch_progress_event`` so
        the optional ``on_preview`` callback can route preview PNGs
        to the right UI surface (a per-layer preview thumbnail).
        """
        throttle_s = max(0.0, float(self._prefs.ui.preview_throttle_ms) / 1000.0)
        last_applied_t = -float("inf")
        pending: ProgressEvent | None = None
        observed: list[ProgressEvent] = []
        cancelled = False
        async for event in handle.progress():
            observed.append(event)
            if event.kind == "cancelled":
                cancelled = True
                break
            pending, last_applied_t = self._dispatch_progress_event(
                event=event,
                state=state,
                layer_id=layer_id,
                pending=pending,
                last_applied_t=last_applied_t,
                throttle_s=throttle_s,
            )
        # Safety flush — gateways that omit a terminal event must not
        # leave the latest preview unapplied. Skipped on cancel since
        # the cancel branch clears generation state anyway.
        if not cancelled and pending is not None:
            self._apply_progress_to_state(state, pending)
            self._maybe_invoke_on_preview(layer_id, pending)
        return observed, cancelled

    def _dispatch_progress_event(
        self,
        *,
        event: ProgressEvent,
        state: AIEffectState,
        layer_id: str,
        pending: ProgressEvent | None,
        last_applied_t: float,
        throttle_s: float,
    ) -> tuple[ProgressEvent | None, float]:
        """Decide whether ``event`` applies now or is held as pending.

        Returns the new ``(pending, last_applied_t)`` pair. When an
        event passes the throttle, the optional ``on_preview``
        callback is invoked iff it carries a non-empty ``preview_png``
        — so a UI consumer sees at most one preview per
        ``ui.preview_throttle_ms`` window (latest-only) without doing
        any backpressure work itself.
        """
        if event.kind in ("started", "done", "error"):
            if pending is not None:
                self._apply_progress_to_state(state, pending)
                self._maybe_invoke_on_preview(layer_id, pending)
            self._apply_progress_to_state(state, event)
            self._maybe_invoke_on_preview(layer_id, event)
            return None, self._progress_clock()
        if throttle_s <= 0.0:
            self._apply_progress_to_state(state, event)
            self._maybe_invoke_on_preview(layer_id, event)
            return None, self._progress_clock()
        now = self._progress_clock()
        if now - last_applied_t >= throttle_s:
            self._apply_progress_to_state(state, event)
            self._maybe_invoke_on_preview(layer_id, event)
            return None, now
        # Coalesce: keep only the latest in-window event so the most
        # recent preview_png / progress wins (latest-only).
        return event, last_applied_t

    def _maybe_invoke_on_preview(self, layer_id: str, event: ProgressEvent) -> None:
        """Forward a throttle-passed event's ``preview_png`` to the
        on_preview callback when both are present.

        Failures inside the callback are logged but never propagate —
        a UI sink crashing must not interrupt progress consumption.
        """
        if self._on_preview is None:
            return
        png = event.preview_png
        if not png:
            return
        try:
            self._on_preview(layer_id, png)
        except Exception:
            logger.warning("on_preview callback raised", exc_info=True)

    # ------------------------------------------------------------------
    # Internal: state mutations
    # ------------------------------------------------------------------

    @staticmethod
    def _update_generation_state(state: AIEffectState, job_id: str, started_at: datetime) -> None:
        state.generation = GenerationState(job_id=job_id, started_at=started_at)
        state.state = LayerState.GENERATING.value

    @staticmethod
    def _apply_progress_to_state(state: AIEffectState, event: ProgressEvent) -> None:
        if state.generation is None:
            return
        if event.progress is not None:
            state.generation.progress = event.progress
        if event.step is not None:
            state.generation.step = event.step
        if event.max_step is not None:
            state.generation.max_step = event.max_step

    @staticmethod
    def _clear_generation_state(state: AIEffectState) -> None:
        state.generation = None

    @staticmethod
    def _apply_cache_hit_to_state(
        state: AIEffectState,
        cached: GenerationRecord,
        param_hash: str,
    ) -> None:
        state.last_result = LastResult(
            param_hash=param_hash,
            finished_at=_parse_iso(cached.completed_at or cached.created_at),
            mode=_extract_mode_from_record(cached),
        )


def _parse_iso(value: str) -> datetime:
    """Parse ISO-8601 (supports trailing Z). Returns UTC now on failure."""
    if not value:
        return datetime.now(UTC)
    try:
        # Accept "...Z" by swapping for +00:00 which fromisoformat handles.
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)


def _extract_mode_from_record(record: GenerationRecord) -> str:
    snapshot = record.service_snapshot
    if isinstance(snapshot, dict):
        payload = snapshot.get("ai_effect")
        if isinstance(payload, dict):
            mode = payload.get("mode")
            if mode in ("self", "new_layer_above"):
                return str(mode)
    return "self"


# Silence reference to imported symbols for future wiring
_ = JobHandle
