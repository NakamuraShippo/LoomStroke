"""GPT Image 2 Gateway adapter over :class:`GPTImage2Service`.

This mirrors the NanoBanana AI Effect bridge: GPT Image 2 has no workflow
graph, so authoring exposes a synthetic parameter catalogue and runtime
delegates to the existing service plugin. The gateway never reads or stores
API keys; ``GPTImage2Service`` remains the only network/credential boundary.
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
from loomstroke.plugins.gptimage2.input_images import MAX_INPUT_LAYERS
from loomstroke.plugins.gptimage2.types import (
    ALL_BACKGROUNDS,
    ALL_MODELS,
    ALL_MODERATION,
    ALL_OUTPUT_FORMATS,
    ALL_QUALITIES,
    ALL_SIZES,
    DEFAULT_MODEL,
)

logger = logging.getLogger(__name__)


def _param_catalogue() -> list[ParamCandidate]:
    """Return the canonical GPT Image 2 parameter surface."""
    return [
        ParamCandidate(
            source_hint="gptimage2.prompt",
            suggested_display_name="Prompt",
            suggested_type="string",
            suggested_default="",
            suggested_range=None,
            suggested_choices=None,
            gateway_ref={"param": "prompt"},
            suggested_multiline=True,
        ),
        ParamCandidate(
            source_hint="gptimage2.model",
            suggested_display_name="Model",
            suggested_type="enum",
            suggested_default=DEFAULT_MODEL,
            suggested_range=None,
            suggested_choices=list(ALL_MODELS),
            gateway_ref={"param": "model"},
        ),
        ParamCandidate(
            source_hint="gptimage2.size",
            suggested_display_name="Size",
            suggested_type="enum",
            suggested_default="1024x1024",
            suggested_range=None,
            suggested_choices=list(ALL_SIZES),
            gateway_ref={"param": "size"},
        ),
        ParamCandidate(
            source_hint="gptimage2.quality",
            suggested_display_name="Quality",
            suggested_type="enum",
            suggested_default="auto",
            suggested_range=None,
            suggested_choices=list(ALL_QUALITIES),
            gateway_ref={"param": "quality"},
        ),
        ParamCandidate(
            source_hint="gptimage2.output_format",
            suggested_display_name="Output format",
            suggested_type="enum",
            suggested_default="png",
            suggested_range=None,
            suggested_choices=list(ALL_OUTPUT_FORMATS),
            gateway_ref={"param": "output_format"},
        ),
        ParamCandidate(
            source_hint="gptimage2.output_compression",
            suggested_display_name="Output compression",
            suggested_type="int",
            suggested_default=100,
            suggested_range={"min": 0, "max": 100, "step": 1},
            suggested_choices=None,
            gateway_ref={"param": "output_compression"},
        ),
        ParamCandidate(
            source_hint="gptimage2.background",
            suggested_display_name="Background",
            suggested_type="enum",
            suggested_default="auto",
            suggested_range=None,
            suggested_choices=list(ALL_BACKGROUNDS),
            gateway_ref={"param": "background"},
        ),
        ParamCandidate(
            source_hint="gptimage2.moderation",
            suggested_display_name="Moderation",
            suggested_type="enum",
            suggested_default="auto",
            suggested_range=None,
            suggested_choices=list(ALL_MODERATION),
            gateway_ref={"param": "moderation"},
        ),
    ]


def _output_catalogue() -> list[OutputCandidate]:
    return [
        OutputCandidate(
            source_hint="gptimage2.output_image",
            suggested_display_name="Output image",
            gateway_ref={"output": "primary"},
        )
    ]


class _ServiceBackedJobHandle(JobHandle):
    """Bridge GPTImage2Service stream/result to AIGateway events."""

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
        async for event in self._service.stream(self.job_id):
            kind = "done" if event.step >= event.total else "progress"
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
        result = await self._service.result(self.job_id)
        duration = time.monotonic() - self._submitted_at
        if not result.success:
            return JobResult(
                status="error",
                output_images=[],
                duration_sec=duration,
                error=AIError(
                    class_=ErrorClass.BACKEND,
                    title="GPT Image 2 job failed",
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
        return JobResult(
            status="completed" if outputs else "error",
            output_images=outputs,
            duration_sec=duration,
            error=(
                None
                if outputs
                else AIError(
                    class_=ErrorClass.BACKEND,
                    title="GPT Image 2 returned no image",
                    detail="result.output was empty",
                )
            ),
        )

    async def cancel(self) -> None:
        try:
            await self._service.cancel(self.job_id)
        except Exception:
            logger.debug("GPT Image 2 cancel failed for job %s", self.job_id, exc_info=True)


def _resolve_input_images(
    request: ExecutionRequest,
    param_mappings: dict[str, Any],
) -> list[bytes]:
    ordered_slots = []
    for order, (internal_id, ref) in enumerate(param_mappings.items()):
        if not isinstance(ref, dict) or ref.get("slot") != "input_image":
            continue
        index = ref.get("index", order)
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            index = order
        ordered_slots.append((index, order, internal_id))
    input_pngs: list[bytes] = []
    for _, _, internal_id in sorted(ordered_slots):
        slot = request.slot_images.get(internal_id)
        if slot is not None and slot.png_bytes:
            input_pngs.append(bytes(slot.png_bytes))
        elif slot is not None or request.param_values.get(internal_id):
            raise AIGatewayError(
                AIError(
                    class_=ErrorClass.VALIDATION,
                    title="Input image must be resolved before submit",
                    detail=f"input_slot={internal_id}",
                )
            )
    return input_pngs


class GPTImage2Gateway(AIGateway):
    """AIGateway that authors GPT Image 2 presets and delegates runtime."""

    gateway_id = "gptimage2"
    display_name = "GPT Image 2"

    def __init__(self, service: Any, *, display_name: str | None = None) -> None:
        self._service = service
        self._disposed = False
        if display_name is not None:
            self.display_name = display_name

    def load_workflow_file(self, path: str) -> WorkflowLoadResult:
        del path
        return WorkflowLoadResult(ok=True, workflow={})

    def detect_candidates(
        self,
        workflow: dict[str, Any],
        *,
        object_info: dict[str, Any] | None = None,
    ) -> CandidateSet:
        del workflow, object_info
        return CandidateSet(
            input_slots=[
                SlotCandidate(
                    source_hint="gptimage2.input_image",
                    suggested_display_name=f"Input image {index + 1}",
                    gateway_ref={"slot": "input_image", "index": index},
                    suggested_default_included=False,
                )
                for index in range(MAX_INPUT_LAYERS)
            ],
            parameters=_param_catalogue(),
            outputs=_output_catalogue(),
        )

    def validate_gateway_file(  # noqa: PLR0911 - linear failure-mode chart
        self, gateway_file: dict[str, Any]
    ) -> ValidationResult:
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
        del gateway_file
        return None

    async def check_status(self) -> GatewayStatus:
        endpoint = "https://api.openai.com/v1/images/generations"
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
            logger.debug("GPT Image 2 key check failed", exc_info=True)
        return GatewayStatus(
            connected=healthy and is_key_configured,
            endpoint=endpoint,
            extra={
                "healthy": healthy,
                "api_key_configured": is_key_configured,
            },
        )

    async def check_dependencies(self, gateway_file: dict[str, Any]) -> DependencyReport:
        del gateway_file
        check = getattr(self._service, "is_api_key_configured", None)
        if not callable(check) or check():
            return DependencyReport(ok=True)
        display_name = "GPT Image 2"
        dependency_name = "gptimage2"
        display = getattr(self._service, "api_key_display_name", None)
        if callable(display):
            try:
                display_name = str(display() or display_name)
            except Exception:
                logger.debug("GPT Image 2 dependency display lookup failed", exc_info=True)
        ref = getattr(self._service, "api_key_ref", None)
        if callable(ref):
            try:
                service_name, username = ref()
                dependency_name = f"{service_name}/{username}"
            except Exception:
                logger.debug("GPT Image 2 dependency key lookup failed", exc_info=True)
        return DependencyReport(
            ok=False,
            missing=[
                MissingDependency(
                    kind="api_key",
                    name=dependency_name,
                    hint=(
                        f"Set the {display_name} API key in "
                        "Preferences -> AI Effect -> API Keys."
                    ),
                )
            ],
        )

    async def submit(self, request: ExecutionRequest) -> JobHandle:
        from loomstroke.plugins.sdk.base import JobSpec  # noqa: PLC0415

        if self._disposed:
            raise AIGatewayError(
                AIError(class_=ErrorClass.UNKNOWN, title="Gateway already disposed")
            )
        gateway_file = request.gateway_file or {}
        param_mappings = gateway_file.get("param_mappings") or {}
        if not isinstance(param_mappings, dict):
            param_mappings = {}

        canonical: dict[str, Any] = {}
        for internal_id, ref in param_mappings.items():
            if not isinstance(ref, dict):
                continue
            param_key = ref.get("param")
            if isinstance(param_key, str) and internal_id in request.param_values:
                canonical[param_key] = request.param_values[internal_id]

        prompt = str(canonical.pop("prompt", "") or "").strip()
        if not prompt:
            raise AIGatewayError(
                AIError(
                    class_=ErrorClass.VALIDATION,
                    title="Prompt is required",
                    detail="GPT Image 2 submit needs a non-empty prompt parameter.",
                )
            )

        options: dict[str, Any] = {}
        for key in (
            "model",
            "size",
            "quality",
            "output_format",
            "output_compression",
            "background",
            "moderation",
        ):
            if key in canonical and canonical[key] not in (None, ""):
                options[key] = canonical[key]

        input_pngs = _resolve_input_images(request, param_mappings)
        params_payload = {
            "prompt": prompt,
            "options": options,
            "apply_to_layer": False,
            "input_pngs": input_pngs,
        }
        try:
            job_id = await self._service.submit(JobSpec(op="generate", params=params_payload))
        except (RuntimeError, ValueError) as exc:
            raise AIGatewayError(
                AIError(
                    class_=ErrorClass.BACKEND,
                    title="GPT Image 2 submit failed",
                    detail=str(exc),
                )
            ) from exc

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

    async def dispose(self) -> None:
        self._disposed = True


__all__ = ["GPTImage2Gateway"]
