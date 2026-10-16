"""AI Effect Layer runtime data model (dataclasses only).

Spec reference: ``AIeffect_design/04_python_design/data_model.md`` §1-§2.

Phase 2-1 provides the dataclasses that will be embedded into ``_Layer``
via the ``ai_effect`` and ``origin`` fields (added in Phase 2-2) and into
``GenerateAIEffectCommand`` (Phase 2-4). No integration with ``_Layer`` or
undo/redo yet — this file is pure data definitions.

Design principles (data_model.md intro):
- Extend existing ``_Layer`` with ``layer_type="ai_effect"`` variant
- UUID-centric layer references (no weakref / path strings)
- Snapshot approach: ``.lsfx`` content is copied into ``AIEffectState`` on
  attach; preset updates do not auto-propagate
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SlotRef:
    """Reference from an AI Effect Layer input slot to a source ``_Layer``.

    Attributes:
        layer_id: UUID of the referenced ``_Layer``.
        exclude_blend: If True, the source layer is excluded from the main
            canvas composite and is used only as AI input. Independent of
            ``_Layer.visible``. Does NOT participate in ``param_hash``
            (see data_model.md §1 notes).
    """

    layer_id: str
    exclude_blend: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise for ``.lstroke`` persistence (Phase 2-6-5).

        Matches the shape documented in data_model.md §7:
        ``{"layer_id": "...", "exclude_blend": bool}``.
        """
        return {"layer_id": self.layer_id, "exclude_blend": bool(self.exclude_blend)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SlotRef:
        """Restore a ``SlotRef`` from a persisted dict.

        ``layer_id`` is required; ``exclude_blend`` defaults to False when
        absent so older documents without the field still load.
        """
        return cls(
            layer_id=str(data["layer_id"]),
            exclude_blend=bool(data.get("exclude_blend", False)),
        )


@dataclass
class GenerationState:
    """Transient state while a generation job is running.

    Present only while the owning ``AIEffectState.state`` is in
    ``queued`` or ``generating``. Set to None otherwise.
    """

    job_id: str
    started_at: datetime
    progress: float | None = None
    step: int | None = None
    max_step: int | None = None


@dataclass
class LastResult:
    """Metadata for the last successful generation.

    Used by the UI to show the cache badge and "2秒前" relative time.

    Attributes:
        param_hash: Gallery lookup key (gallery_integration.md §2).
        finished_at: Wall-clock time the result landed.
        mode: Output mode — ``"self"`` overwrites the AIEffectLayer's own
            image; ``"new_layer_above"`` creates sibling output layers.
        output_layer_ids: Non-empty only in ``new_layer_above`` mode, listing
            the generated output layer UUIDs.
    """

    param_hash: str
    finished_at: datetime
    mode: str
    output_layer_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for ``.lstroke`` persistence (Phase 2-6-5).

        ``finished_at`` is written as an ISO 8601 string (datetime is
        not JSON-native). output_layer_ids is copied defensively so the
        persistence layer cannot mutate the runtime state.
        """
        return {
            "param_hash": self.param_hash,
            "finished_at": self.finished_at.isoformat(),
            "mode": self.mode,
            "output_layer_ids": list(self.output_layer_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LastResult:
        """Restore a ``LastResult`` from a persisted dict.

        Accepts both ISO 8601 string (the canonical persisted form) and
        a ``datetime`` instance already (used in tests that skip the
        serialise step).
        """
        raw_finished = data["finished_at"]
        if isinstance(raw_finished, datetime):
            finished_at = raw_finished
        else:
            finished_at = datetime.fromisoformat(str(raw_finished))
        return cls(
            param_hash=str(data["param_hash"]),
            finished_at=finished_at,
            mode=str(data["mode"]),
            output_layer_ids=[str(x) for x in data.get("output_layer_ids", [])],
        )


@dataclass
class AIError:
    """Minimal error record for the AIEffectState.last_error field.

    This is intentionally a thin structural record. Full error taxonomy
    (``ErrorClass`` etc.) lives in :mod:`loomstroke.plugins.ai_effect.errors`
    and is the canonical type for Gateway → UI error propagation.

    Kept here as a forward-compat placeholder so data_model.md §1.5 can
    reference ``AIEffectState.last_error``. Phase 4 controller wiring will
    store :class:`loomstroke.plugins.ai_effect.errors.AIError` instances
    directly — the type ``AIEffectState.last_error`` is therefore the
    canonical ``errors.AIError`` (not this placeholder).
    """

    title: str
    detail: str | None = None


@dataclass
class AIEffectState:
    """Per-layer AI Effect state (embedded in ``_Layer.ai_effect``).

    Preset-derived fields are snapshotted on attach so preset updates don't
    silently mutate already-used AIEffectLayers.
    """

    # ---- Preset identification / snapshot ----
    preset_id: str
    preset_snapshot: dict[str, Any]
    """Deep copy of the .lsfx ``manifest.json`` and matching
    ``gateways/{gateway_id}.json`` at attach time."""

    gateway_file_hash: str
    """sha256 of the gateway_file JSON — cached so param_hash can compute
    without re-hashing on every keystroke."""

    gateway_id: str

    # ---- User-facing parameter values ----
    param_values: dict[str, Any] = field(default_factory=dict)
    """``internal_id`` → value. Initialized from ``manifest.parameters[].default``."""

    param_ui_state: dict[str, Any] = field(default_factory=dict)
    """``internal_id`` → LoomStroke-only parameter UI state.

    This stores editor affordances such as LivePrompt chip composition state.
    Gateways continue to receive only ``param_values``; this metadata must not
    be copied into ComfyUI workflow JSON.
    """

    slot_refs: dict[str, SlotRef | None] = field(default_factory=dict)
    """``internal_id`` → SlotRef or None. Required slots retain the key even
    when unset (None) so the UI can detect missing inputs."""

    trigger: str = "manual"
    """One of ``"manual"`` / ``"on_change"`` / ``"debounced"``."""

    keep_history: bool = False
    """In ``new_layer_above`` mode, True adds a new output layer per
    generation; False overwrites the previous output layer."""

    output_mode_override: str | None = None
    """Per-instance override of the preset's per-output ``mode``.

    When ``None`` (default), each output uses the mode declared in
    ``manifest.outputs[].mode`` at preset-author time. When set to
    ``"self"`` / ``"new_layer_above"``, every output of this layer is
    forced to that mode regardless of the preset.

    Phase 6-h: exposes "output to this layer" as a layer-level common
    setting in the Effects panel — the user can flip a configured
    ``new_layer_above`` preset to dump back into the AI Effect layer
    itself (the layer doubles as a raster container) without re-
    authoring the preset.
    """

    target_server_id: str | None = None
    """Phase 10c: id of the registered ComfyUI server this layer's
    generations should be submitted to. ``None`` means "use the
    registry's default server" — which preserves the pre-10c
    single-server behaviour for users who never touch the multi-server
    UI. The id resolves through
    :class:`loomstroke.plugins.comfyui.server_registry.ComfyUIServerRegistry`
    so a renamed server keeps working as long as the id stays stable.

    Forward-compat fields ``fallback_server_id`` and
    ``workflow_origin_server_id`` from spec §4.5 are NOT included
    in this slice — they ship with Phase 10d (validation/fallback)
    where they actually have a consumer.
    """

    # ---- Runtime state ----
    state: str = "idle"
    """One of ``"idle"`` / ``"queued"`` / ``"generating"`` / ``"done"``
    / ``"error"``. Mirrors :class:`loomstroke.plugins.ai_effect.controller.LayerState`
    string values."""

    generation: GenerationState | None = None
    last_result: LastResult | None = None
    last_error: Any | None = None
    """Canonical type is ``loomstroke.plugins.ai_effect.errors.AIError`` when
    present (Phase 4 wiring). Typed as ``Any`` here to avoid a circular
    import between data_model and errors; Phase 4 will add a runtime
    assertion or TypeAlias if needed."""

    def to_dict(self) -> dict[str, Any]:
        """Serialise for ``.lstroke`` persistence (Phase 2-6-5).

        Emits the shape documented in data_model.md §7. Runtime-only
        fields (``state`` / ``generation`` / ``last_error``) are
        deliberately omitted — reopening a document should not resume
        an in-flight job or resurrect an already-dismissed error.

        ``preset_snapshot`` is deep-copied so later runtime mutations
        cannot silently mutate the persisted bytes.
        """
        slot_refs_dict: dict[str, Any] = {}
        for slot_id, ref in self.slot_refs.items():
            if ref is None:
                slot_refs_dict[slot_id] = None
            elif isinstance(ref, SlotRef):
                slot_refs_dict[slot_id] = ref.to_dict()
            elif isinstance(ref, dict):
                # Tolerate a dict-shaped SlotRef stand-in (used by tests
                # that inject raw dicts).
                slot_refs_dict[slot_id] = dict(ref)
            else:
                # Unknown shape — drop the slot rather than crash. The
                # author can re-wire on next open.
                slot_refs_dict[slot_id] = None
        out: dict[str, Any] = {
            "preset_id": self.preset_id,
            "preset_snapshot": copy.deepcopy(self.preset_snapshot),
            "gateway_id": self.gateway_id,
            "gateway_file_hash": self.gateway_file_hash,
            "param_values": copy.deepcopy(self.param_values),
            "slot_refs": slot_refs_dict,
            "trigger": self.trigger,
            "keep_history": bool(self.keep_history),
            "last_result": (self.last_result.to_dict() if self.last_result is not None else None),
        }
        if self.param_ui_state:
            out["param_ui_state"] = copy.deepcopy(self.param_ui_state)
        # Only emit ``output_mode_override`` when the user has actually
        # set it — keeps existing .lstroke files byte-identical and
        # avoids polluting the schema with a None field on every layer.
        if self.output_mode_override is not None:
            out["output_mode_override"] = str(self.output_mode_override)
        # Phase 10c: same opt-in policy for the per-layer server target.
        # When ``None`` we leave the field absent so a single-server
        # user's .lstroke files round-trip byte-identical to pre-10c.
        if self.target_server_id is not None:
            out["target_server_id"] = str(self.target_server_id)
        return out

    @classmethod
    def from_preset(cls, preset: Any) -> AIEffectState:
        """Build an ``AIEffectState`` seeded from an ``AIEffectPreset``.

        Used by Phase 7-h-A Filter menu path (and future Preset Manager)
        to create a fresh AIEffectLayer bound to a library preset. The
        resulting state has:

        - ``preset_snapshot`` = ``{"manifest": ..., "gateway_file": ...}``
          (deep-copied so the library entry and the layer diverge after
          attach — data_model.md §2 snapshot semantics).
        - ``param_values`` populated from ``manifest.parameters[].default``.
        - ``slot_refs`` empty; the author wires sources via Layers Panel.
        - ``trigger`` from ``manifest.execution.trigger`` when present,
          else ``"manual"``.

        ``preset`` is typed ``Any`` to avoid an import of
        ``plugins.ai_effect.preset`` from inside this module — duck-typed
        on ``.preset_id`` / ``.manifest`` / ``.gateway_file_hash`` /
        ``.gateway_id`` / ``.primary_gateway_file()``.
        """
        manifest = getattr(preset, "manifest", {}) or {}
        gateway_file = preset.primary_gateway_file() or {}

        params_raw = manifest.get("parameters", []) if isinstance(manifest, dict) else []
        param_values: dict[str, Any] = {}
        if isinstance(params_raw, list):
            for entry in params_raw:
                if not isinstance(entry, dict):
                    continue
                internal_id = entry.get("internal_id")
                if not isinstance(internal_id, str) or not internal_id:
                    continue
                if "default" in entry:
                    param_values[internal_id] = entry["default"]

        trigger = "manual"
        execution = manifest.get("execution") if isinstance(manifest, dict) else None
        if isinstance(execution, dict):
            t = execution.get("trigger")
            if isinstance(t, str) and t:
                trigger = t

        snapshot = {
            "manifest": copy.deepcopy(manifest),
            "gateway_file": copy.deepcopy(gateway_file),
        }
        return cls(
            preset_id=str(preset.preset_id),
            preset_snapshot=snapshot,
            gateway_file_hash=str(getattr(preset, "gateway_file_hash", "")),
            gateway_id=str(preset.gateway_id),
            param_values=param_values,
            slot_refs={},
            trigger=trigger,
            keep_history=False,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AIEffectState:
        """Restore an ``AIEffectState`` from a persisted dict.

        Raises ``KeyError`` or ``ValueError`` on malformed input so the
        caller (page_service / document_service) can log a warning and
        fall back to an un-attached layer rather than silently loading
        corrupt state.
        """
        raw_slot_refs = data.get("slot_refs", {})
        if not isinstance(raw_slot_refs, dict):
            raise ValueError("slot_refs must be a dict")
        slot_refs: dict[str, SlotRef | None] = {}
        for slot_id, ref_data in raw_slot_refs.items():
            if ref_data is None:
                slot_refs[str(slot_id)] = None
            elif isinstance(ref_data, dict):
                slot_refs[str(slot_id)] = SlotRef.from_dict(ref_data)
            else:
                raise ValueError(
                    f"slot_refs[{slot_id!r}] must be None or a dict, got {type(ref_data).__name__}"
                )

        raw_last_result = data.get("last_result")
        last_result: LastResult | None = None
        if isinstance(raw_last_result, dict):
            last_result = LastResult.from_dict(raw_last_result)

        preset_snapshot = data.get("preset_snapshot", {})
        if not isinstance(preset_snapshot, dict):
            raise ValueError("preset_snapshot must be a dict")

        raw_override = data.get("output_mode_override")
        output_mode_override: str | None = None
        if isinstance(raw_override, str) and raw_override in ("self", "new_layer_above"):
            output_mode_override = raw_override

        raw_target_server = data.get("target_server_id")
        target_server_id: str | None = None
        if isinstance(raw_target_server, str) and raw_target_server.strip():
            target_server_id = raw_target_server.strip()

        raw_param_ui_state = data.get("param_ui_state", {})
        param_ui_state = (
            copy.deepcopy(raw_param_ui_state) if isinstance(raw_param_ui_state, dict) else {}
        )

        return cls(
            preset_id=str(data["preset_id"]),
            preset_snapshot=copy.deepcopy(preset_snapshot),
            gateway_file_hash=str(data.get("gateway_file_hash", "")),
            gateway_id=str(data["gateway_id"]),
            param_values=dict(data.get("param_values", {})),
            param_ui_state=param_ui_state,
            slot_refs=slot_refs,
            trigger=str(data.get("trigger", "manual")),
            keep_history=bool(data.get("keep_history", False)),
            output_mode_override=output_mode_override,
            target_server_id=target_server_id,
            last_result=last_result,
            # Runtime fields — always start fresh on load (spec §7).
            state="idle",
            generation=None,
            last_error=None,
        )


@dataclass
class LayerOrigin:
    """Provenance metadata on generated output layers.

    Attached to ordinary ``_Layer`` instances that were created by an
    AIEffectLayer's ``new_layer_above`` output. Allows UI to distinguish
    "generated" vs "user-authored" layers and enables undo logic to
    correctly revert output-layer creation (Phase 2-4).

    Attributes:
        type: ``"ai_effect_output"`` / ``"import"`` / ``"user_created"`` / ...
            Phase 2-1 only introduces ``"ai_effect_output"`` formally; other
            values are reserved for future origin tracking.
        parent_effect_id: UUID of the generating AIEffectLayer when
            ``type == "ai_effect_output"``, else None.
        output_slot_id: ``manifest.outputs[].internal_id`` (e.g. ``"output_0"``).
        generation_index: Monotonic counter per parent+slot, used when
            ``keep_history=True`` so a chronological history is queryable.
    """

    type: str
    parent_effect_id: str | None = None
    output_slot_id: str | None = None
    generation_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise for ``.lstroke`` persistence (Phase 2-6-5)."""
        return {
            "type": self.type,
            "parent_effect_id": self.parent_effect_id,
            "output_slot_id": self.output_slot_id,
            "generation_index": int(self.generation_index),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LayerOrigin:
        """Restore a ``LayerOrigin`` from a persisted dict."""
        raw_parent = data.get("parent_effect_id")
        raw_slot = data.get("output_slot_id")
        return cls(
            type=str(data["type"]),
            parent_effect_id=str(raw_parent) if raw_parent is not None else None,
            output_slot_id=str(raw_slot) if raw_slot is not None else None,
            generation_index=int(data.get("generation_index", 0)),
        )
