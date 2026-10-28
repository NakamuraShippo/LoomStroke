"""NanoBanana Gateway — :class:`AIGateway` adapter over NanoBananaService.

Phase 8a-bridge (2026-04-29): the original Phase 8a goal of "NanoBanana
Gateway in the AI Effect registry" was deferred while 8a got expanded
into the API-key security overhaul. This module closes that gap.

Design parallels the ComfyUI gateway:

- **Delegate runtime to NanoBananaService**: the existing NanoBanana
  plugin already owns the Gemini SDK call, the read-on-demand API key
  flow. This gateway requests PNG assets without direct layer application;
  ``submit`` builds a ``JobSpec`` and hands off to
  ``NanoBananaService.submit`` / ``stream`` / ``result``.

- **No workflow-file concept**: NanoBanana is a parameter-only API;
  there is no node graph to load. ``load_workflow_file`` raises
  ``NotImplementedError`` per the protocol contract, and
  ``extract_raw_workflow`` returns ``None``.

- **Synthetic candidates**: ``detect_candidates`` ignores the
  ``workflow`` argument (callers pass ``{}``) and returns the
  canonical NanoBanana parameter surface — modality / aspect_ratio /
  model / image_size / thinking_level / include_thoughts /
  google_search / prompt — plus N input-image slots and a single
  output slot. The Editor renders these as ParamCandidates without
  needing to know NanoBanana-specific shapes.

- **No QObject / Signal**: returns async iterators / coroutines.

- **Non-destructive on legacy plugin**: read-only access to the
  NanoBanana service. The only public surface is
  ``NanoBananaGateway(service)`` and ``register`` in ``app.py``.
"""

from __future__ import annotations

import copy
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

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
    ParamCandidate,
    ParamChoice,
    ProgressEvent,
    SlotCandidate,
    SlotChoice,
    ValidationResult,
    WorkflowLoadResult,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Canonical parameter catalogue
# ----------------------------------------------------------------------

# Maximum input layers NanoBanana itself accepts. Mirrors
# ``loomstroke.plugins.nanobanana.types.MAX_INPUT_LAYERS`` — intentionally
# inlined to avoid importing the nanobanana plugin at module import time.
_MAX_INPUT_LAYERS = 14

_ASPECT_CHOICES = [
    "1:1",
    "1:4",
    "1:8",
    "2:3",
    "3:2",
    "3:4",
    "4:1",
    "4:3",
    "4:5",
    "5:4",
    "8:1",
    "9:16",
    "16:9",
    "21:9",
]
_MODALITY_CHOICES = ["Image", "Text+Image"]
_IMAGE_SIZE_CHOICES = ["", "512", "1K", "2K", "4K"]
_THINKING_LEVEL_CHOICES = ["minimal", "High"]


def _param_catalogue() -> list[ParamCandidate]:
    """Return the canonical NanoBanana parameter surface as ParamCandidates."""
    # Resolve lazily so registry imports do not activate the service plugin.
    from loomstroke.plugins.nanobanana.types import ALL_MODELS, DEFAULT_MODEL  # noqa: PLC0415

    return [
        ParamCandidate(
            source_hint="nanobanana.prompt",
            suggested_display_name="Prompt",
            suggested_type="string",
            suggested_default="",
            suggested_range=None,
            suggested_choices=None,
            gateway_ref={"param": "prompt"},
            suggested_multiline=True,
        ),
        ParamCandidate(
            source_hint="nanobanana.model",
            suggested_display_name="Model",
            suggested_type="enum",
            suggested_default=DEFAULT_MODEL,
            suggested_range=None,
            suggested_choices=list(ALL_MODELS),
            gateway_ref={"param": "model"},
        ),
        ParamCandidate(
            source_hint="nanobanana.modality",
            suggested_display_name="Modality",
            suggested_type="enum",
            suggested_default="Text+Image",
            suggested_range=None,
            suggested_choices=list(_MODALITY_CHOICES),
            gateway_ref={"param": "modality"},
        ),
        ParamCandidate(
            source_hint="nanobanana.aspect_ratio",
            suggested_display_name="Aspect ratio",
            suggested_type="enum",
            suggested_default="1:1",
            suggested_range=None,
            suggested_choices=list(_ASPECT_CHOICES),
            gateway_ref={"param": "aspect_ratio"},
        ),
        ParamCandidate(
            source_hint="nanobanana.image_size",
            suggested_display_name="Image size",
            suggested_type="enum",
            suggested_default="",
            suggested_range=None,
            suggested_choices=list(_IMAGE_SIZE_CHOICES),
            gateway_ref={"param": "image_size"},
        ),
        ParamCandidate(
            source_hint="nanobanana.thinking_level",
            suggested_display_name="Thinking level",
            suggested_type="enum",
            suggested_default="minimal",
            suggested_range=None,
            suggested_choices=list(_THINKING_LEVEL_CHOICES),
            gateway_ref={"param": "thinking_level"},
        ),
        ParamCandidate(
            source_hint="nanobanana.include_thoughts",
            suggested_display_name="Include thoughts",
            suggested_type="bool",
            suggested_default=False,
            suggested_range=None,
            suggested_choices=None,
            gateway_ref={"param": "include_thoughts"},
        ),
        ParamCandidate(
            source_hint="nanobanana.google_search",
            suggested_display_name="Google Search",
            suggested_type="bool",
            suggested_default=False,
            suggested_range=None,
            suggested_choices=None,
            gateway_ref={"param": "google_search"},
        ),
    ]


def _slot_catalogue() -> list[SlotCandidate]:
    """Return the canonical input slot surface (one slot per input layer).

    Only ``Input image 1`` (index 0) starts ticked. The author opts in
    to additional slots as needed — most NanoBanana presets need a
    single input image (img2img / refine / restyle) and forcing all
    14 to start ticked would dump 13 unused candidates into the export
    that the author has to manually untick.
    """
    return [
        SlotCandidate(
            source_hint="nanobanana.input_image",
            suggested_display_name=f"Input image {i + 1}",
            gateway_ref={"slot": "input_image", "index": i},
            suggested_default_included=(i == 0),
        )
        for i in range(_MAX_INPUT_LAYERS)
    ]


def _output_catalogue() -> list[OutputCandidate]:
    """Return the canonical output slot surface (one image)."""
    return [
        OutputCandidate(
            source_hint="nanobanana.output_image",
            suggested_display_name="Output image",
            gateway_ref={"output": "primary"},
        )
    ]


def _ordered_slot_mappings(param_mappings: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(internal_id, ref)`` slot mappings in NanoBanana input order."""
    ordered: list[tuple[int, int, str, dict[str, Any]]] = []
    fallback_order = 0
    for internal_id, ref in param_mappings.items():
        if not isinstance(internal_id, str) or not isinstance(ref, dict):
            continue
        if "slot" not in ref:
            continue
        raw_index = ref.get("index")
        index = (
            raw_index
            if isinstance(raw_index, int) and not isinstance(raw_index, bool)
            else fallback_order
        )
        ordered.append((index, fallback_order, internal_id, ref))
        fallback_order += 1
    ordered.sort(key=lambda item: (item[0], item[1]))
    return [(internal_id, ref) for _index, _fallback, internal_id, ref in ordered]


# ----------------------------------------------------------------------
# JobHandle — wraps NanoBananaService stream/result for the AI Effect runtime
# ----------------------------------------------------------------------


class _ServiceBackedJobHandle(JobHandle):
    """Bridge from :class:`NanoBananaService` events to AIGateway events.

    The service emits its own ``ProgressEvent`` shape (``job_id`` / ``step``
    / ``total`` / ``message`` / ``preview_png``) and produces a terminal
    ``JobResult`` once the job finishes. This handle forwards progress
    events through the gateway protocol and finalises the AIGateway
    ``JobResult`` (with ``OutputImage`` list extracted from the service's
    binary output) once the service signals done.
    """

    def __init__(
        self,
        service: Any,
        job_id: str,
        request_id: str,
        output_internal_id: str,
    ) -> None:
        self._service = service
        self.job_id = job_id
        self.request_id = request_id
        self._output_internal_id = output_internal_id
        self._submitted_at = time.monotonic()

    async def progress(self) -> AsyncIterator[ProgressEvent]:
        """Yield gateway-protocol ProgressEvents derived from the service stream."""
        async for event in self._service.stream(self.job_id):
            kind = "progress"
            if event.step >= event.total:
                kind = "done"
            yield ProgressEvent(
                kind=kind,
                progress=(event.step / event.total) if event.total else None,
                step=event.step,
                max_step=event.total,
                preview_png=event.preview_png,
                message=event.message,
            )
            if kind == "done":
                break

    async def wait(self) -> JobResult:
        """Return the terminal ``JobResult`` once the underlying job ends.

        Does NOT iterate ``self.progress()``. The AI Effect controller's
        ``_consume_progress`` already drains the event stream before
        calling ``wait``; a second iteration would call
        ``NanoBananaService.stream`` again, hit ``queue.get()`` on an
        empty queue, and block forever (the producer task finished
        emitting the terminal "completed" event during the first
        drain). ``service.result`` awaits the underlying task itself,
        so the terminal state is guaranteed to be visible by the time
        we read it — no manual drain is needed.
        """
        result = await self._service.result(self.job_id)
        duration = time.monotonic() - self._submitted_at
        if not result.success:
            return JobResult(
                status="error",
                output_images=[],
                duration_sec=duration,
                error=AIError(
                    class_=ErrorClass.BACKEND,
                    title="NanoBanana job failed",
                    detail=result.error or "unknown",
                ),
            )
        output_bytes: bytes | None = None
        raw_output = getattr(result, "output", None)
        if isinstance(raw_output, (bytes, bytearray)):
            output_bytes = bytes(raw_output)
        elif isinstance(getattr(result, "data", None), (bytes, bytearray)):
            output_bytes = bytes(result.data)
        outputs: list[OutputImage] = []
        if output_bytes:
            outputs.append(
                OutputImage(
                    internal_id=self._output_internal_id,
                    png_bytes=output_bytes,
                    width=0,
                    height=0,
                )
            )
        # Status string MUST be ``"completed"`` (not ``"done"``) — the
        # AIEffectGenerationController checks ``result.status !=
        # "completed"`` to decide between the success and error paths.
        # User report (2026-04-30): NanoBanana generations applied to
        # the canvas but never reached the gallery and the layer-row
        # Generate button stayed grey because ``"done"`` slipped into
        # the error path. Align with ComfyUI's value here.
        return JobResult(
            status="completed" if outputs else "error",
            output_images=outputs,
            duration_sec=duration,
            error=(
                None
                if outputs
                else AIError(
                    class_=ErrorClass.BACKEND,
                    title="NanoBanana returned no image",
                    detail="result.output was empty",
                )
            ),
        )

    async def cancel(self) -> None:
        """Best-effort cancel — delegates to ``NanoBananaService.cancel``."""
        try:
            await self._service.cancel(self.job_id)
        except Exception:
            logger.debug("NanoBanana cancel failed for job %s", self.job_id, exc_info=True)


# ----------------------------------------------------------------------
# Gateway implementation
# ----------------------------------------------------------------------


class NanoBananaGateway(AIGateway):
    """:class:`AIGateway` that authors NanoBanana presets and delegates
    runtime to :class:`NanoBananaService`.

    NanoBanana is parameter-only — there is no workflow file to load.
    The Editor's Gateway picker lists this alongside ComfyUI; the
    candidate list is synthesised from the canonical parameter
    catalogue rather than parsed from a workflow.
    """

    gateway_id = "nanobanana"
    display_name = "NanoBanana"

    def __init__(self, service: Any, *, display_name: str | None = None) -> None:
        """Accept the NanoBanana plugin's :class:`NanoBananaService`.

        Typed ``Any`` to keep this module Qt-import-free; the attribute
        surface used here is duck-typed (``submit`` / ``stream`` /
        ``result`` / ``cancel`` / ``health`` / ``is_api_key_configured``).
        """
        self._service = service
        self._disposed = False
        if display_name is not None:
            self.display_name = display_name

    # ------------------------------------------------------------------
    # Authoring API
    # ------------------------------------------------------------------

    def load_workflow_file(self, path: str) -> WorkflowLoadResult:
        """NanoBanana has no workflow file. Returns the empty-workflow stub.

        The Editor uses this method's success path to decide whether a
        Gateway needs a workflow picker. NanoBanana doesn't, so we
        return ``ok=True`` with an empty workflow dict — that lets the
        Editor proceed straight to ``detect_candidates`` without
        prompting the user for a file.
        """
        del path
        return WorkflowLoadResult(ok=True, workflow={})

    def detect_candidates(
        self,
        workflow: dict[str, Any],
        *,
        object_info: dict[str, Any] | None = None,
    ) -> CandidateSet:
        """Return the canonical NanoBanana surface.

        ``workflow`` and ``object_info`` are accepted for protocol
        symmetry but ignored — NanoBanana has a fixed parameter surface
        that does not depend on a workflow shape or backend
        introspection.
        """
        del workflow, object_info
        return CandidateSet(
            input_slots=_slot_catalogue(),
            parameters=_param_catalogue(),
            outputs=_output_catalogue(),
        )

    def validate_gateway_file(  # noqa: PLR0911 - linear failure-mode chart
        self, gateway_file: dict[str, Any]
    ) -> ValidationResult:
        """Verify a NanoBanana ``gateways/nanobanana.json``."""
        if not isinstance(gateway_file, dict):
            return ValidationResult(ok=False, reason="not_dict")
        sv = gateway_file.get("schema_version")
        if not isinstance(sv, str):
            return ValidationResult(ok=False, reason="missing_schema_version")
        if sv.split(".", 1)[0] != "1":
            return ValidationResult(
                ok=False, reason="schema_major_mismatch", detail=f"schema_version={sv}"
            )
        param_mappings = gateway_file.get("param_mappings")
        if not isinstance(param_mappings, dict):
            return ValidationResult(ok=False, reason="missing_param_mappings")
        # Each entry binds an internal_id to one of three kinds:
        # - ``"param": "<canonical-name>"`` (parameter)
        # - ``"slot": "input_image", "index": <int>`` (input slot)
        # - ``"output": "primary"`` (output)
        for internal_id, ref in param_mappings.items():
            if not isinstance(ref, dict):
                return ValidationResult(
                    ok=False,
                    reason="mapping_not_dict",
                    detail=f"internal_id={internal_id}",
                )
            if not any(key in ref for key in ("param", "slot", "output")):
                return ValidationResult(
                    ok=False,
                    reason="mapping_missing_kind",
                    detail=f"internal_id={internal_id}",
                )
        return ValidationResult(ok=True)

    def apply_author_choices(
        self,
        raw_workflow: dict[str, Any] | None,
        choices: AuthorChoices,
    ) -> dict[str, Any]:
        """Build ``gateways/nanobanana.json`` from Editor selections.

        NanoBanana has no workflow graph, so ``raw_workflow`` is
        ignored. The ``param_mappings`` simply records which canonical
        NanoBanana parameter each internal_id binds to.
        """
        del raw_workflow
        param_mappings: dict[str, Any] = {}
        for s in choices.input_slots:
            param_mappings[s.internal_id] = copy.deepcopy(s.gateway_ref)
        for p in choices.parameters:
            param_mappings[p.internal_id] = copy.deepcopy(p.gateway_ref)
        for o in choices.outputs:
            param_mappings[o.internal_id] = copy.deepcopy(o.gateway_ref)
        return {
            "schema_version": "1.0",
            "param_mappings": param_mappings,
        }

    def parse_author_choices(
        self,
        raw_workflow: dict[str, Any] | None,
        gateway_file: dict[str, Any],
    ) -> AuthorChoices:
        """Restore Editor selections from a saved NanoBanana gateway file."""
        del raw_workflow
        slots: list[SlotChoice] = []
        params: list[ParamChoice] = []
        outputs: list[OutputChoice] = []
        mappings = gateway_file.get("param_mappings") or {}
        if not isinstance(mappings, dict):
            return AuthorChoices(slots, params, outputs, [])
        for internal_id, ref in mappings.items():
            if not isinstance(internal_id, str) or not isinstance(ref, dict):
                continue
            if "slot" in ref or internal_id.startswith("slot_"):
                slots.append(
                    SlotChoice(
                        internal_id=internal_id,
                        display_name=internal_id,
                        required=False,
                        order=len(slots),
                        gateway_ref=dict(ref),
                    )
                )
            elif "output" in ref or internal_id.startswith("output_"):
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
        """NanoBanana has no workflow. Always returns ``None``."""
        del gateway_file
        return None

    # ------------------------------------------------------------------
    # Runtime API (delegation to NanoBananaService)
    # ------------------------------------------------------------------

    async def check_status(self) -> GatewayStatus:
        """Return service health + API-key status as a GatewayStatus.

        ``connected=True`` requires (a) the service's ``health`` probe
        succeeds, and (b) the API key is configured. The Effect Panel
        uses this for the connection badge — both conditions matter
        because a configured key with a dead network produces 0%
        success rate, and a working network with no key produces 100%
        AUTH errors.
        """
        endpoint = "https://generativelanguage.googleapis.com"
        try:
            healthy = bool(await self._service.health())
        except Exception as exc:  # noqa: BLE001
            return GatewayStatus(connected=False, endpoint=endpoint, extra={"error": str(exc)})
        is_key_configured = False
        try:
            check = getattr(self._service, "is_api_key_configured", None)
            if callable(check):
                is_key_configured = bool(check())
        except Exception:
            logger.debug("NanoBanana key check failed", exc_info=True)
        return GatewayStatus(
            connected=healthy and is_key_configured,
            endpoint=endpoint,
            extra={
                "healthy": healthy,
                "api_key_configured": is_key_configured,
            },
        )

    async def check_dependencies(self, gateway_file: dict[str, Any]) -> DependencyReport:
        """NanoBanana depends only on the API key being set.

        The service checks the key at submit time (read-on-demand) so
        this is a structural pre-flight: warn the user before they
        click Generate if the key is absent.
        """
        del gateway_file
        check = getattr(self._service, "is_api_key_configured", None)
        if not callable(check):
            return DependencyReport(ok=True)
        if check():
            return DependencyReport(ok=True)
        return DependencyReport(
            ok=False,
            missing=[
                MissingDependency(
                    kind="api_key",
                    name="nanobanana",
                    hint="Set the NanoBanana API key in Preferences → AI Effect → API Keys.",
                )
            ],
        )

    async def submit(  # noqa: PLR0912 - flat guard chain for param resolution
        self, request: ExecutionRequest
    ) -> JobHandle:
        """Build a NanoBanana ``JobSpec`` and hand off to the service.

        Maps the AIGateway ``ExecutionRequest`` (preset-driven param
        values + slot images keyed by internal_id) onto NanoBanana's
        flat parameter surface (prompt + options + input_layer_ids).
        """
        from loomstroke.plugins.sdk.base import JobSpec  # noqa: PLC0415

        if self._disposed:
            raise AIGatewayError(
                AIError(class_=ErrorClass.UNKNOWN, title="Gateway already disposed")
            )
        gateway_file = request.gateway_file or {}
        param_mappings = gateway_file.get("param_mappings") or {}
        if not isinstance(param_mappings, dict):
            param_mappings = {}

        # Resolve param_mappings + request.param_values into the
        # canonical NanoBanana parameter dict.
        canonical: dict[str, Any] = {}
        for internal_id, ref in param_mappings.items():
            if not isinstance(ref, dict):
                continue
            param_key = ref.get("param")
            if not isinstance(param_key, str):
                continue
            if internal_id in request.param_values:
                canonical[param_key] = request.param_values[internal_id]

        prompt = str(canonical.pop("prompt", "") or "").strip()
        if not prompt:
            raise AIGatewayError(
                AIError(
                    class_=ErrorClass.VALIDATION,
                    title="Prompt is required",
                    detail="NanoBanana submit needs a non-empty prompt parameter.",
                )
            )

        options: dict[str, Any] = {}
        for key in (
            "model",
            "modality",
            "aspect_ratio",
            "image_size",
            "thinking_level",
            "include_thoughts",
            "google_search",
        ):
            if key in canonical and canonical[key] not in (None, ""):
                options[key] = canonical[key]

        # Slot images: AIEffect resolves SlotRef bindings before
        # submit and passes pre-composed PNGs in ``request.slot_images``.
        # Older call sites may still place layer IDs in
        # ``request.param_values`` keyed by slot internal_id, so keep
        # that fallback. Prefer PNG bytes here; otherwise NanoBanana
        # receives a text-only request and Gemini often answers with
        # "please upload images" instead of returning an image.
        input_pngs: list[bytes] = []
        input_layer_ids: list[str] = []
        for internal_id, _ref in _ordered_slot_mappings(param_mappings):
            slot_image = request.slot_images.get(internal_id)
            if slot_image is not None and slot_image.png_bytes:
                input_pngs.append(bytes(slot_image.png_bytes))
                continue
            value = request.param_values.get(internal_id)
            if isinstance(value, str) and value:
                input_layer_ids.append(value)
        params_payload: dict[str, Any] = {
            "prompt": prompt,
            "input_layer_ids": input_layer_ids,
            "input_pngs": input_pngs,
            "options": options,
            "apply_to_layer": False,
        }

        spec = JobSpec(op="generate", params=params_payload)
        try:
            job_id = await self._service.submit(spec)
        except (RuntimeError, ValueError) as exc:
            raise AIGatewayError(
                AIError(
                    class_=ErrorClass.BACKEND,
                    title="NanoBanana submit failed",
                    detail=str(exc),
                )
            ) from exc

        # Pick an internal_id for the output. Defaults to "output_primary"
        # if the gateway_file doesn't name one — the Effect controller
        # only needs *something* stable to look up the OutputImage.
        output_internal_id = "output_primary"
        for internal_id, ref in param_mappings.items():
            if isinstance(ref, dict) and "output" in ref:
                output_internal_id = internal_id
                break

        return _ServiceBackedJobHandle(
            service=self._service,
            job_id=job_id,
            request_id=request.request_id or uuid.uuid4().hex,
            output_internal_id=output_internal_id,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def dispose(self) -> None:
        """No owned resources — NanoBananaService manages its own jobs.

        ``NanoBananaService.deactivate`` (called by the plugin teardown
        path) cancels any in-flight tasks itself. The gateway holds no
        aiohttp session of its own, so there is nothing to close here.
        """
        self._disposed = True


__all__ = ["NanoBananaGateway"]
