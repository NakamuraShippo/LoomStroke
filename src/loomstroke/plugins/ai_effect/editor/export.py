"""Preset export pipeline (Phase 7-d).

Thin orchestrator that glues the Effect Editor together:

    CandidatesPane + per-candidate Choice dict + ExportMetadata
        │
        │  collect_author_choices()     ◄── merges edited choices with
        │                                    candidate-derived defaults
        ▼
    AuthorChoices  ────────►  AIGateway.apply_author_choices
        │                          │
        │                          ▼
        │                      gateway_file (dict)
        │
        │  build_manifest()
        ▼
    manifest (dict)
        │
        ▼
    write_lsfx_atomic(path, manifest, {gateway_id: gateway_file}, icon=…)

``default_choice_from_candidate`` intentionally duplicates the "populate
→ build" logic that :class:`ChoiceInspector` applies for unedited
candidates — see ``tests/unit/ai_effect/editor/test_export.py`` for the
round-trip check that keeps the two implementations in sync.
"""

from __future__ import annotations

import copy
import logging
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from loomstroke.plugins.ai_effect.editor.metadata_form import ExportMetadata
from loomstroke.plugins.ai_effect.gateway_protocol import (
    AIGateway,
    AuthorChoices,
    CandidateSet,
    OutputCandidate,
    OutputChoice,
    ParamCandidate,
    ParamChoice,
    SlotCandidate,
    SlotChoice,
)
from loomstroke.plugins.ai_effect.lsfx_io import LsfxReadError, read_lsfx, write_lsfx_atomic
from loomstroke.plugins.ai_effect.manifest_validator import (
    VALID_PARAM_TYPES,
    validate_manifest,
)

logger = logging.getLogger(__name__)

_SANITIZE_RE = re.compile(r"[^a-z0-9_]+")


class ExportValidationError(ValueError):
    """Raised when the author has not supplied enough data to export.

    Caught by the host window and surfaced in the status bar; never
    propagated to the event loop.
    """


# ---------------------------------------------------------------------------
# Candidate identity — stable across CandidatesPane rebuilds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateKey:
    """Stable identity for a candidate across detect_candidates calls.

    Composed of ``(kind, source_hint)`` — the Gateway contract promises
    ``source_hint`` is unique per candidate within a single workflow's
    detect output (``AIeffect_design/04_python_design/gateway_protocol.md``
    §3.4). Using this pair instead of ``id(candidate)`` survives
    ``CandidatesPane`` rebuilds and the Python GC reusing an ``id`` for a
    different object.
    """

    kind: str
    source_hint: str


def candidate_key(
    candidate: SlotCandidate | ParamCandidate | OutputCandidate,
) -> CandidateKey:
    """Return the :class:`CandidateKey` for a candidate instance."""
    if isinstance(candidate, SlotCandidate):
        return CandidateKey("slot", candidate.source_hint)
    if isinstance(candidate, ParamCandidate):
        return CandidateKey("param", candidate.source_hint)
    if isinstance(candidate, OutputCandidate):
        return CandidateKey("output", candidate.source_hint)
    raise TypeError(f"Unknown candidate kind: {type(candidate).__name__}")


# ---------------------------------------------------------------------------
# Candidate → Choice default helpers (mirror ChoiceInspector)
# ---------------------------------------------------------------------------


def _default_internal_id(suggested_name: str, fallback: str) -> str:
    """Slugify a display name into a safe internal_id.

    Mirror of :func:`inspector._default_internal_id`. Kept duplicated to
    avoid reaching into a module that pulls in PySide6; equivalence is
    enforced by ``tests/unit/ai_effect/editor/test_export.py``.
    """
    base = suggested_name.strip().lower()
    cleaned = _SANITIZE_RE.sub("_", base).strip("_")
    return cleaned or fallback


def coerce_param_default(raw: Any, ptype: str) -> Any:  # noqa: PLR0911
    """Coerce a raw suggested_default into the typed value for ``ptype``.

    Mirror of ``_coerce_default(text, ptype)`` in inspector — routes through
    ``str(raw)`` first so both paths hit the same textual coercion logic.

    Unparseable inputs (``"abc"`` for ``int``, unknown keyword for ``bool``)
    silently return ``None`` to match inspector behaviour, but emit a debug
    log first so gateway metadata bugs leave a forensic trail rather than
    disappearing into a ``default: null`` in the exported manifest.
    """
    text = "" if raw is None else str(raw)
    stripped = text.strip()
    if ptype == "bool":
        if stripped.lower() in ("true", "1", "yes", "on"):
            return True
        if stripped.lower() in ("false", "0", "no", "off", ""):
            return False
        logger.debug("coerce default: bool=%r not in accepted vocab → None", raw)
        return None
    if not stripped:
        return None
    try:
        if ptype == "int":
            return int(stripped)
        if ptype == "float":
            return float(stripped)
    except ValueError:
        logger.debug("coerce default: raw=%r ptype=%s parse failed → None", raw, ptype)
        return None
    return stripped


def default_slot_choice(candidate: SlotCandidate) -> SlotChoice:
    return SlotChoice(
        internal_id=_default_internal_id(candidate.suggested_display_name, "slot"),
        display_name=candidate.suggested_display_name,
        required=False,
        order=0,
        gateway_ref=dict(candidate.gateway_ref),
    )


def default_param_choice(candidate: ParamCandidate) -> ParamChoice:
    ptype = candidate.suggested_type if candidate.suggested_type in VALID_PARAM_TYPES else "string"
    default = coerce_param_default(candidate.suggested_default, ptype)
    rng = candidate.suggested_range or {}
    range_min = float(rng.get("min", 0) or 0)
    range_max = float(rng.get("max", 0) or 0)
    range_step = float(rng.get("step", 0) or 0)
    if ptype in ("int", "float") and (range_min != 0 or range_max != 0):
        range_dict: dict[str, Any] | None = {"min": range_min, "max": range_max}
        if range_step > 0:
            range_dict["step"] = range_step
    else:
        range_dict = None
    if ptype == "enum" and candidate.suggested_choices:
        choices: list[Any] | None = [
            str(c).strip() for c in candidate.suggested_choices if str(c).strip()
        ]
        if not choices:
            choices = None
    else:
        choices = None
    return ParamChoice(
        internal_id=_default_internal_id(candidate.suggested_display_name, "param"),
        display_name=candidate.suggested_display_name,
        default=default,
        group=None,
        type=ptype,
        range=range_dict,
        choices=choices,
        required_text=False,
        order=0,
        gateway_ref=dict(candidate.gateway_ref),
    )


def default_output_choice(candidate: OutputCandidate) -> OutputChoice:
    return OutputChoice(
        internal_id=_default_internal_id(candidate.suggested_display_name, "output"),
        display_name=candidate.suggested_display_name,
        mode="new_layer_above",
        overwrite_on_regenerate=True,
        order=0,
        gateway_ref=dict(candidate.gateway_ref),
    )


def default_choice_from_candidate(
    candidate: SlotCandidate | ParamCandidate | OutputCandidate,
) -> SlotChoice | ParamChoice | OutputChoice:
    """Produce the same Choice the inspector would for an untouched candidate.

    Used when the author ticks a candidate's include box but never selects
    it (so the inspector never fires ``choice_changed``). Letting such
    candidates reach the manifest keeps the "tick = include" metaphor from
    silently losing entries at export time.
    """
    if isinstance(candidate, SlotCandidate):
        return default_slot_choice(candidate)
    if isinstance(candidate, ParamCandidate):
        return default_param_choice(candidate)
    if isinstance(candidate, OutputCandidate):
        return default_output_choice(candidate)
    raise TypeError(f"Unknown candidate kind: {type(candidate).__name__}")


# ---------------------------------------------------------------------------
# Collection + manifest build
# ---------------------------------------------------------------------------


def collect_author_choices(
    included: CandidateSet,
    edited: dict[CandidateKey, Any],
) -> AuthorChoices:
    """Build an :class:`AuthorChoices` from the included candidates.

    For each included candidate:
    - If the author edited it (``candidate_key(candidate) in edited``),
      use the stored Choice verbatim.
    - Otherwise, synthesise the default via
      :func:`default_choice_from_candidate`.

    Finally, dedupe ``internal_id`` collisions per section by appending
    ``_2``, ``_3`` … — keeps the manifest validator happy when the author
    ticks multiple untouched candidates that slugify to the same name.
    """
    slots = _collect_section(included.input_slots, edited, SlotChoice)
    params = _collect_section(included.parameters, edited, ParamChoice)
    outputs = _collect_section(included.outputs, edited, OutputChoice)
    _dedupe_internal_ids(slots)
    _dedupe_internal_ids(params)
    _dedupe_internal_ids(outputs)
    return AuthorChoices(
        input_slots=slots,
        parameters=params,
        outputs=outputs,
        parameter_groups=[],
    )


def build_manifest(
    preset_id: str,
    gateway_id: str,
    metadata: ExportMetadata,
    choices: AuthorChoices,
) -> dict[str, Any]:
    """Assemble a ``manifest.json`` dict from metadata + author choices.

    Produces a faithful serialisation of the inputs without sanitising
    unknown categories or triggers — validation is the sole responsibility
    of ``validate_manifest``, called once by :func:`export_preset`. A bad
    trigger therefore surfaces as ``manifest_invalid:invalid_trigger`` to
    the user instead of being silently rewritten to ``"manual"``.
    """
    return {
        "schema_version": "1.0",
        "preset_id": preset_id,
        "gateway": gateway_id,
        "metadata": {
            "name": metadata.name,
            "category": metadata.category,
            "description": metadata.description,
            "tags": list(metadata.tags),
        },
        "input_slots": [_slot_entry(c) for c in choices.input_slots],
        "parameters": [_param_entry(c) for c in choices.parameters],
        "outputs": [_output_entry(c) for c in choices.outputs],
        "parameter_groups": [asdict(g) for g in choices.parameter_groups],
        "execution": {"trigger": metadata.trigger},
    }


def export_preset(
    output_path: Path | str,
    gateway: AIGateway,
    raw_workflow: dict[str, Any] | None,
    choices: AuthorChoices,
    metadata: ExportMetadata,
    *,
    preset_id: str | None = None,
    icon_bytes: bytes | None = None,
    preview_bytes: bytes | None = None,
    endpoint_url: str | None = None,
    target_server_id: str | None = None,
    native_workflow: dict[str, Any] | None = None,
) -> Path:
    """Validate, build, and atomically write a ``.lsfx`` file.

    Returns the final path as :class:`Path` for the caller to surface in
    the status bar. Raises :class:`ExportValidationError` on author-level
    issues (empty name, no outputs) *before* touching the filesystem.

    ``preset_id``: pass ``None`` (the default) to mint a fresh UUID —
    current UI behaviour for first-time export. The explicit form exists
    for the future import / re-export path (Phase 7-f) that needs to
    preserve a preset's identity across edits instead of creating a new
    library entry every time.

    ``endpoint_url``: optional gateway-specific endpoint override. When
    provided as a non-empty string, it is written to
    ``gateway_file["endpoint_url"]`` so the runtime can pick a per-
    preset target (Phase 8a-bridge: ComfyUIGateway.submit reads this
    and swaps its connection's ``base_url`` before submitting).
    Gateways that ignore the field (NanoBanana) do not see any change
    in behaviour.

    ``target_server_id``: optional Phase 10c-ui server-id override
    (``"local"`` / ``"external"`` / a custom registered id). Written
    alongside ``endpoint_url`` so the runtime can resolve the target
    via :class:`ComfyUIServerRegistry` first and fall back to the raw
    URL only when the id is absent. Both fields are independent — a
    preset that selected the Settings-managed Local server stores
    ``target_server_id="local"`` and no ``endpoint_url``; a preset
    that pinned a one-off URL stores ``endpoint_url`` and no
    ``target_server_id``.
    """
    if not metadata.name.strip():
        raise ExportValidationError("metadata_name_empty")
    if not choices.outputs:
        raise ExportValidationError("no_outputs_selected")

    effective_preset_id = preset_id if preset_id is not None else str(uuid.uuid4())
    gateway_file = gateway.apply_author_choices(raw_workflow, choices)
    if gateway.gateway_id == "comfyui" and native_workflow is not None:
        gateway_file["native_workflow"] = copy.deepcopy(native_workflow)
    overrides: dict[str, Any] = {}
    if isinstance(endpoint_url, str) and endpoint_url.strip():
        overrides["endpoint_url"] = endpoint_url.strip()
    if isinstance(target_server_id, str) and target_server_id.strip():
        overrides["target_server_id"] = target_server_id.strip()
    if overrides:
        gateway_file = {**gateway_file, **overrides}
    manifest = build_manifest(effective_preset_id, gateway.gateway_id, metadata, choices)

    validation = validate_manifest(manifest)
    if not validation.ok:
        # Surfaces a generated manifest that fails our own validator —
        # almost always a programmer-level bug rather than user input. Log
        # the manifest snapshot so the author can file a reproducible
        # report without the editor state being lost.
        logger.error(
            "build_manifest produced invalid manifest: reason=%s detail=%s",
            validation.reason,
            validation.detail,
        )
        raise ExportValidationError(f"manifest_invalid:{validation.reason}")

    target = Path(output_path)
    write_lsfx_atomic(
        target,
        manifest,
        {gateway.gateway_id: gateway_file},
        icon_bytes=icon_bytes,
        preview_bytes=preview_bytes,
    )

    # Post-write self-verify: re-read the archive we just produced and
    # confirm the manifest round-trips byte-for-byte. Guards against
    # serialisation bugs inside ``write_lsfx_atomic`` and against disk or
    # external interference between write and user consumption. Cheap
    # because .lsfx files are small (KiB range).
    try:
        readback = read_lsfx(target, source="imported")
    except (LsfxReadError, OSError) as exc:
        logger.error("export_preset writeback unreadable for %s: %s", target, exc)
        target.unlink(missing_ok=True)
        raise ExportValidationError(f"writeback_unreadable:{exc}") from exc
    if readback.manifest != manifest:
        logger.error("export_preset writeback manifest mismatch for %s", target)
        target.unlink(missing_ok=True)
        raise ExportValidationError("writeback_manifest_mismatch")

    logger.info("Exported preset %s to %s", effective_preset_id, target)
    return target


# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------


def _collect_section(
    included_candidates: list[Any],
    edited: dict[CandidateKey, Any],
    expected_choice_type: type,
) -> list[Any]:
    result: list[Any] = []
    for cand in included_candidates:
        stored = edited.get(candidate_key(cand))
        if isinstance(stored, expected_choice_type):
            result.append(copy.deepcopy(stored))
        else:
            result.append(default_choice_from_candidate(cand))
    return result


def _dedupe_internal_ids(entries: list[Any]) -> None:
    """Mutate ``entries`` so every ``internal_id`` is unique.

    Collision strategy: append ``_2``, ``_3`` … on subsequent duplicates,
    **skipping values already taken by other entries** so an author-typed
    ``foo_2`` never collides with the auto-generated suffix from another
    ``foo``. Preserves the first occurrence of each verbatim name so
    authored names are stable across re-exports of the same workflow.
    """
    taken: set[str] = set()
    for entry in entries:
        name = entry.internal_id or ""
        if name not in taken:
            taken.add(name)
            continue
        k = 2
        while f"{name}_{k}" in taken:
            k += 1
        entry.internal_id = f"{name}_{k}"
        taken.add(entry.internal_id)


def _slot_entry(c: SlotChoice) -> dict[str, Any]:
    return {
        "internal_id": c.internal_id,
        "display_name": c.display_name,
        "required": bool(c.required),
        "order": int(c.order),
    }


def _param_entry(c: ParamChoice) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "internal_id": c.internal_id,
        "display_name": c.display_name,
        "type": c.type,
        "default": c.default,
        "order": int(c.order),
        "required_text": bool(c.required_text),
    }
    if c.group:
        entry["group"] = c.group
    if c.range is not None:
        entry["range"] = dict(c.range)
    if c.choices is not None:
        entry["choices"] = list(c.choices)
    return entry


def _output_entry(c: OutputChoice) -> dict[str, Any]:
    return {
        "internal_id": c.internal_id,
        "display_name": c.display_name,
        "mode": c.mode,
        "overwrite_on_regenerate": bool(c.overwrite_on_regenerate),
        "order": int(c.order),
    }


__all__ = [
    "CandidateKey",
    "ExportValidationError",
    "build_manifest",
    "candidate_key",
    "coerce_param_default",
    "collect_author_choices",
    "default_choice_from_candidate",
    "default_output_choice",
    "default_param_choice",
    "default_slot_choice",
    "export_preset",
]
