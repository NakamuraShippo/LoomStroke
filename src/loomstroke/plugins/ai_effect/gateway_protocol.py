"""AI Effect Gateway protocol — abstract types and auxiliary dataclasses.

Spec reference: ``AIeffect_design/04_python_design/gateway_protocol.md``
§1 (design principles) / §2 (AIGateway ABC) / §3 (dataclasses).

Phase 1 (Foundation) delivers the abstract surface. Real Gateway implementations
land in Phase 5 (ComfyUI) and Phase 8a (NanoBanana). A FakeGateway for testing
arrives in Phase 1-5.

Responsibility boundaries:
- UI layer knows ``AIGateway``, ``AIEffectState`` (data_model.md), ``SlotRef``
- Gateway implementations know their backend workflow format / HTTP / auth
- ``gateway_file`` (``gateways/{gateway_id}.json``) shape is opaque to the UI;
  only the owning Gateway interprets it
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from loomstroke.plugins.ai_effect.errors import AIError

# ================================================================
# Execution request / response dataclasses (§3.1 / §3.2)
# ================================================================


@dataclass(frozen=True)
class SlotImage:
    """One input slot payload passed to Gateway.

    The UI layer pre-composes the referenced layer (including child blending)
    into an RGBA image, encodes as PNG, and hands the bytes over. Gateway
    passes this through to the backend without decoding.
    """

    png_bytes: bytes
    width: int
    height: int
    exclude_blend: bool = False


@dataclass(frozen=True)
class ExecutionRequest:
    """Immutable request for one generation job.

    Frozen so ``param_hash`` (computed externally) stays consistent across the
    submit → progress → done pipeline.
    """

    gateway_file: dict
    manifest: dict
    param_values: dict[str, Any]
    slot_images: dict[str, SlotImage]
    param_hash: str
    layer_id: str
    request_id: str


@dataclass
class ProgressEvent:
    """Progress update from Gateway backend."""

    kind: str
    progress: float | None = None
    step: int | None = None
    max_step: int | None = None
    preview_png: bytes | None = None
    message: str | None = None


@dataclass
class OutputImage:
    """One output slot result from a finished job."""

    internal_id: str
    png_bytes: bytes
    width: int
    height: int


@dataclass
class OutputModel:
    """One 3D model output slot result from a finished job."""

    internal_id: str
    node_id: str
    filename: str
    subfolder: str = ""
    folder_type: str = "output"


@dataclass
class JobResult:
    """Terminal result of a generation job."""

    status: str
    output_images: list[OutputImage]
    duration_sec: float
    error: AIError | None = None
    output_models: list[OutputModel] = field(default_factory=list)


class JobHandle(ABC):
    """Handle to a submitted generation job.

    Concrete implementations provide async iteration over progress events,
    a shortcut ``wait()`` for just the result, and cooperative ``cancel()``.
    """

    job_id: str
    request_id: str

    @abstractmethod
    def progress(self) -> AsyncIterator[ProgressEvent]:
        """Yield progress events until terminal (done / error).

        NOTE (code review #12): concrete subclasses commonly implement this as
        ``async def progress(...)`` with ``yield`` statements (async generator
        function). At runtime both forms are callable and return an
        AsyncIterator, so the contract holds. However, strict type checkers
        may flag the mismatch between the ABC (regular ``def`` returning
        AsyncIterator) and the async-generator-function implementation. If
        mypy --strict surfaces this in the future, unify by switching the ABC
        to ``async def ... -> AsyncIterator[...]: ...``; this requires updating
        ``FakeGateway`` and any other Gateway that already follows the async
        generator pattern — which is all of them today.
        """

    @abstractmethod
    async def wait(self) -> JobResult:
        """Wait for terminal result, consuming progress events internally."""

    @abstractmethod
    async def cancel(self) -> None:
        """Best-effort cancellation request to backend."""


# ================================================================
# Preset authoring types (§preset_authoring.md §2)
# ================================================================


@dataclass
class SlotChoice:
    """User-selected input slot in Effect Editor."""

    internal_id: str
    display_name: str
    required: bool
    order: int
    gateway_ref: dict


@dataclass
class ParamChoice:
    """User-selected exposed parameter in Effect Editor."""

    internal_id: str
    display_name: str
    default: Any
    group: str | None
    type: str
    range: dict | None
    choices: list[Any] | None
    required_text: bool
    order: int
    gateway_ref: dict


@dataclass
class OutputChoice:
    """User-selected output in Effect Editor."""

    internal_id: str
    display_name: str
    mode: str
    overwrite_on_regenerate: bool
    order: int
    gateway_ref: dict


@dataclass
class GroupChoice:
    """Parameter group metadata (for collapse state and ordering)."""

    name: str
    order: int
    collapsed: bool


@dataclass
class AuthorChoices:
    """Aggregated Effect Editor selections.

    Input to ``AIGateway.apply_author_choices`` (creates gateway_file.json)
    and output of ``AIGateway.parse_author_choices`` (restores from imported
    .lsfx).
    """

    input_slots: list[SlotChoice]
    parameters: list[ParamChoice]
    outputs: list[OutputChoice]
    parameter_groups: list[GroupChoice]


# ================================================================
# Workflow / candidate detection types (§3.4)
# ================================================================


@dataclass
class WorkflowLoadResult:
    ok: bool
    workflow: dict | None = None
    error: str | None = None


@dataclass
class SlotCandidate:
    source_hint: str
    suggested_display_name: str
    gateway_ref: dict
    # Whether this candidate starts ticked when the Editor populates
    # a fresh CandidateSet (``populate(default_included=True)``).
    # Defaults to ``True`` so existing gateways stay source-compatible.
    # NanoBanana's catalogue exposes 14 input-image slots but only
    # the first is checked-by-default — the author opts in to the
    # additional 13 instead of opting out of every unused one.
    suggested_default_included: bool = True


@dataclass
class ParamCandidate:
    source_hint: str
    suggested_display_name: str
    suggested_type: str
    suggested_default: Any
    suggested_range: dict | None
    suggested_choices: list[Any] | None
    gateway_ref: dict
    # Optional metadata sourced from a backend's introspection API
    # (ComfyUI's ``/object_info``). When present, the Editor / Effect
    # Panel can render correctly-bounded widgets instead of guessing
    # from the scalar default. Default ``False`` keeps existing
    # gateways and tests source-compatible.
    suggested_multiline: bool = False
    # Mirrors :class:`SlotCandidate.suggested_default_included` —
    # default ``True`` keeps the existing fresh-workflow UX where
    # heuristic-detected parameters start ticked.
    suggested_default_included: bool = True


@dataclass
class OutputCandidate:
    source_hint: str
    suggested_display_name: str
    gateway_ref: dict
    # Mirrors :class:`SlotCandidate.suggested_default_included`.
    suggested_default_included: bool = True


@dataclass
class CandidateSet:
    """All candidates Effect Editor may offer the author."""

    input_slots: list[SlotCandidate]
    parameters: list[ParamCandidate]
    outputs: list[OutputCandidate]


# ================================================================
# Validation / status / dependency reports (§3.4 cont.)
# ================================================================


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None = None
    detail: str | None = None


@dataclass
class GatewayStatus:
    connected: bool
    endpoint: str
    version: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class MissingDependency:
    kind: str
    name: str
    hint: str | None = None


@dataclass
class DependencyReport:
    ok: bool
    missing: list[MissingDependency] = field(default_factory=list)


# ================================================================
# AIGateway abstract base (§2)
# ================================================================


class AIGateway(ABC):
    """Abstract backend interface for AI Effect Layer.

    One instance per registered backend per process (held by GatewayRegistry).
    Gateway instances are stateless re: per-preset configuration — all preset
    data flows through ``ExecutionRequest``.
    """

    #: Identifier matching manifest.json ``gateway`` field.
    gateway_id: str

    #: Human-readable display name for settings UI.
    display_name: str

    # ------------------------------------------------------------------
    # Authoring API (Effect Editor usage)
    # ------------------------------------------------------------------

    @abstractmethod
    def load_workflow_file(self, path: str) -> WorkflowLoadResult:
        """Load and parse a workflow file (ComfyUI .json etc.).

        NanoBanana-style gateways with no file concept raise ``NotImplementedError``.
        """

    @abstractmethod
    def detect_candidates(
        self,
        workflow: dict,
        *,
        object_info: dict | None = None,
    ) -> CandidateSet:
        """Extract authorable slots / parameters / outputs from a workflow.

        When ``object_info`` is supplied, the gateway should consult it
        to populate ``ParamCandidate.suggested_range`` /
        ``suggested_choices`` / ``suggested_type`` / ``suggested_multiline``
        from the backend's introspection API (e.g. ComfyUI's
        ``/object_info``). When ``None`` or empty, the gateway falls
        back to scalar-shape inference and the Editor surfaces the
        constraint-less defaults — historically the only mode, which
        produced widgets with arbitrary ranges that ignored the actual
        backend's accepted values.
        """

    @abstractmethod
    def validate_gateway_file(self, gateway_file: dict) -> ValidationResult:
        """Verify ``gateways/{gateway_id}.json`` dict against this Gateway's schema."""

    @abstractmethod
    def apply_author_choices(
        self,
        raw_workflow: dict | None,
        choices: AuthorChoices,
    ) -> dict:
        """Build ``gateways/{gateway_id}.json`` content from Editor selections.

        Spec: preset_authoring.md §3.
        """

    @abstractmethod
    def parse_author_choices(
        self,
        raw_workflow: dict | None,
        gateway_file: dict,
    ) -> AuthorChoices:
        """Restore Editor selections from a saved gateway_file (Import path).

        Spec: preset_authoring.md §3.2.
        """

    @abstractmethod
    def extract_raw_workflow(self, gateway_file: dict) -> dict | None:
        """Return the raw workflow (ComfyUI) or ``None`` (NanoBanana) from gateway_file."""

    # ------------------------------------------------------------------
    # Runtime API (AI Effect Layer usage)
    # ------------------------------------------------------------------

    @abstractmethod
    async def check_status(self) -> GatewayStatus:
        """Return connection status for the Effect Panel badge."""

    @abstractmethod
    async def check_dependencies(self, gateway_file: dict) -> DependencyReport:
        """Verify backend has the custom nodes / models / API keys this preset needs."""

    @abstractmethod
    async def submit(self, request: ExecutionRequest) -> JobHandle:
        """Submit job to backend and return a handle. Does NOT wait for completion.

        Raises ``AIGatewayError`` on submission failure (network / 401 / etc.).
        """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def dispose(self) -> None:
        """Close aiohttp sessions / release resources. Called once at shutdown."""
