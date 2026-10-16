"""Gallery integration — ``GenerationRecord`` ↔ AI Effect payload conversion.

Spec reference: ``AIeffect_design/04_python_design/gallery_integration.md``
§3 (GenerationRecord mapping) / §3.2 (service_snapshot.ai_effect schema).

The existing ``GenerationRecord`` dataclass
(``src/loomstroke/core/generation_record.py``) is AI-plugin common. AI
Effect Layer piggybacks on it by writing its extra metadata inside
``service_snapshot.ai_effect`` plus a few conventional top-level fields:

- ``service_id``   → gateway_id ("comfyui" / "nanobanana" / ...)
- ``service_name`` → Gateway.display_name
- ``seed``         → param_values["seed"] when present
- ``prompt``       → first string-typed param value when present
- ``model_name``   → param_values["model"] or param_values["model_name"]
- ``width/height`` → from first OutputImage

This module is **pure conversion** — no Gallery I/O. Controller (Phase 4-3)
hands the resulting GenerationRecord to whatever GenerationHistoryStore the
host provides.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from loomstroke.core.generation_record import GenerationRecord
from loomstroke.plugins.ai_effect.data_model import AIEffectState, SlotRef
from loomstroke.plugins.ai_effect.gateway_protocol import JobResult

logger = logging.getLogger(__name__)


AI_EFFECT_PAYLOAD_VERSION = "1.0"


def build_generation_record(
    *,
    job_id: str,
    gateway_id: str,
    gateway_display_name: str,
    state: AIEffectState,
    result: JobResult,
    param_hash: str,
    layer_versions: dict[str, int],
    source_layer_id: str,
    source_page_id: str | None = None,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
    gateway_extra: dict[str, Any] | None = None,
) -> GenerationRecord:
    """Build a GenerationRecord from a successful JobResult and AIEffectState.

    Args:
        job_id: Gateway-issued job id (returned in JobHandle.job_id).
        gateway_id: manifest.gateway (e.g. ``"comfyui"``).
        gateway_display_name: Gateway.display_name for user-facing rows.
        state: AIEffectState snapshot at submit time (for preset_snapshot +
            gateway_file_hash + slot_refs + param_values).
        result: Terminal JobResult returned by the Gateway.
        param_hash: Pre-computed param_hash (gallery_integration.md §2.4 —
            callers pass the *post-resolve* hash after seed=-1 handling).
        layer_versions: layer_id → texture_version snapshot used when
            computing param_hash. Copied into service_snapshot for audit.
        source_layer_id: UUID of the AIEffectLayer that triggered this job.
        source_page_id: Optional page UUID for multi-page documents.
        created_at: Submit timestamp (defaults to "now" in local time).
        completed_at: Result-arrival timestamp (defaults to "now" in local time).
        gateway_extra: Optional Gateway-specific opaque metadata (queue
            position, backend version, etc.). Stored untouched.

    Returns:
        GenerationRecord ready to pass to ``GenerationHistoryStore.append``.
        ``image_path`` / ``thumbnail_path`` are intentionally left as
        ``None`` — the caller (Controller + storage layer) decides where to
        write the PNG and fills those in.
    """
    # Local-time-with-offset to match the ComfyUI panel's record shape
    # (``datetime.now().astimezone().isoformat()``). The Gallery panel
    # slices ``record.created_at[11:16]`` to display HH:MM and does not
    # convert UTC → local; using local time here keeps the displayed
    # clock matching the user's wall clock for both providers.
    now_iso = _iso(datetime.now().astimezone())
    created_iso = _iso(created_at) if created_at is not None else now_iso
    completed_iso = _iso(completed_at) if completed_at is not None else now_iso

    # ---- Extract convenience top-level fields from state / result ----
    seed = state.param_values.get("seed") if isinstance(state.param_values, dict) else None
    manifest = _safe_get(state.preset_snapshot, ["manifest"], None)
    prompt = _first_text_value(
        state.param_values,
        manifest if isinstance(manifest, dict) else None,
    )
    model_name = _extract_model_name(state.param_values)

    width: int | None = None
    height: int | None = None
    if result.output_images:
        width = int(result.output_images[0].width)
        height = int(result.output_images[0].height)

    # ---- service_snapshot.ai_effect per gallery_integration.md §3.2 ----
    ai_effect_payload: dict[str, Any] = {
        "version": AI_EFFECT_PAYLOAD_VERSION,
        "param_hash": param_hash,
        "preset_id": state.preset_id,
        "preset_name": _safe_get(state.preset_snapshot, ["manifest", "metadata", "name"], ""),
        "preset_snapshot_digest": state.gateway_file_hash,
        "gateway_id": gateway_id,
        "gateway_file_hash": state.gateway_file_hash,
        "mode": _last_mode_from_state(state),
        "param_values": dict(state.param_values),
        "slot_refs": _serialize_slot_refs(state.slot_refs, layer_versions),
        "source_layer_id": source_layer_id,
        "source_page_id": source_page_id,
    }

    service_snapshot: dict[str, Any] = {"ai_effect": ai_effect_payload}
    if gateway_extra is not None:
        service_snapshot["gateway_extra"] = dict(gateway_extra)

    output_images = [
        {
            "internal_id": out.internal_id,
            "width": int(out.width),
            "height": int(out.height),
        }
        for out in result.output_images
    ]

    return GenerationRecord(
        job_id=job_id,
        service_id=gateway_id,
        service_name=gateway_display_name,
        status=result.status,
        created_at=created_iso,
        completed_at=completed_iso,
        duration_sec=result.duration_sec,
        prompt=prompt,
        model_name=model_name,
        seed=int(seed) if isinstance(seed, (int, float)) else None,
        width=width,
        height=height,
        service_snapshot=service_snapshot,
        output_images=output_images,
    )


def extract_ai_effect_payload(record: GenerationRecord) -> dict[str, Any] | None:
    """Pull the ``ai_effect`` payload back out of a GenerationRecord.

    Returns ``None`` if the record was produced by a non-AI-Effect path
    (backwards compatibility with existing Gallery entries from the
    pre-AI-Effect ComfyUI plugin).
    """
    snapshot = record.service_snapshot
    if not isinstance(snapshot, dict):
        return None
    payload = snapshot.get("ai_effect")
    if not isinstance(payload, dict):
        return None
    return payload


def matches_param_hash(record: GenerationRecord, param_hash: str) -> bool:
    """Return True iff the record's ai_effect payload carries ``param_hash``.

    Safe to call on non-AI records (returns False).
    """
    payload = extract_ai_effect_payload(record)
    if payload is None:
        return False
    return payload.get("param_hash") == param_hash


def matches_source_layer(record: GenerationRecord, layer_id: str) -> bool:
    """Return True iff the record was produced by the AIEffectLayer
    identified by ``layer_id``.

    Used by UI to surface per-layer history (gallery_integration.md §4).
    """
    payload = extract_ai_effect_payload(record)
    if payload is None:
        return False
    return payload.get("source_layer_id") == layer_id


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------


def _iso(value: datetime) -> str:
    # Persist with a 'Z' suffix if UTC, otherwise keep offset
    if value.tzinfo is not None and value.utcoffset() == UTC.utcoffset(value):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return value.isoformat()


def _safe_get(payload: Any, path: list[str], default: Any) -> Any:
    node: Any = payload
    for key in path:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
        if node is None:
            return default
    return node


def _first_text_value(
    params: dict[str, Any] | None,
    manifest: dict[str, Any] | None = None,
) -> str | None:
    """Pick the user-facing prompt string out of ``params``.

    When ``manifest`` is available we iterate ``manifest["parameters"]`` in
    declaration order and return the first string-typed param whose value
    in ``params`` is non-empty. This matches the authoring UI's field order
    so a preset shaped like ``{"negative_prompt": ..., "prompt": ...}`` does
    not accidentally surface ``negative_prompt`` just because it sorts first
    alphabetically.

    When no manifest is available (legacy / corrupt state snapshot), we fall
    back to the old alpha-sorted scan and log a debug note so operators can
    tell cached records apart from manifest-aware ones.
    """
    if not isinstance(params, dict):
        return None

    if isinstance(manifest, dict):
        param_defs = manifest.get("parameters")
        if isinstance(param_defs, list):
            for entry in param_defs:
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") != "string":
                    continue
                internal_id = entry.get("internal_id")
                if not isinstance(internal_id, str):
                    continue
                value = params.get(internal_id)
                if isinstance(value, str) and value.strip():
                    return value
            # Manifest present but exhausted — nothing to return.
            return None

    logger.debug("gallery_bridge: falling back to sorted-key prompt scan (manifest unavailable)")
    for key in sorted(params):
        value = params[key]
        if isinstance(value, str) and value.strip():
            return value
    return None


_MODEL_KEYS = ("model", "model_name", "checkpoint")


def _extract_model_name(params: dict[str, Any] | None) -> str | None:
    if not isinstance(params, dict):
        return None
    for key in _MODEL_KEYS:
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _serialize_slot_refs(
    slot_refs: dict[str, SlotRef | None],
    layer_versions: dict[str, int],
) -> dict[str, dict[str, Any] | None]:
    out: dict[str, dict[str, Any] | None] = {}
    for internal_id in sorted(slot_refs):
        ref = slot_refs[internal_id]
        if ref is None:
            out[internal_id] = None
        else:
            out[internal_id] = {
                "layer_id": ref.layer_id,
                "texture_version": int(layer_versions.get(ref.layer_id, 0)),
                "exclude_blend": bool(ref.exclude_blend),
            }
    return out


def _last_mode_from_state(state: AIEffectState) -> str:
    """Prefer state.last_result.mode when already set; otherwise 'self'."""
    last = state.last_result
    if last is not None and getattr(last, "mode", None) in ("self", "new_layer_above"):
        return str(last.mode)
    # Fall back: look at preset_snapshot outputs[0].mode if available
    outputs = _safe_get(state.preset_snapshot, ["manifest", "outputs"], [])
    if isinstance(outputs, list) and outputs:
        first = outputs[0]
        if isinstance(first, dict):
            mode = first.get("mode")
            if mode in ("self", "new_layer_above"):
                return str(mode)
    return "self"
