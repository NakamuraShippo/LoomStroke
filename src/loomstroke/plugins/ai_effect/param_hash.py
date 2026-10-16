"""param_hash — deterministic hash of an AI Effect generation's inputs.

Spec reference: ``AIeffect_design/04_python_design/gallery_integration.md`` §2.

Used for:
- Gallery cache lookup ("ping-pong" between nearby parameter values reusing
  prior generation output without re-running the backend).
- Identifying a Gallery record that matches the current AIEffectLayer state.

Performance target: ``~100 μs`` (sha256 + json.dumps on ~1 KB). Keeps this
function safely outside the 60fps stroke path.

Canonicalization rules:
- Dict keys sorted so encoding is order-independent.
- Floats formatted as fixed-6 strings for cross-platform stability
  (avoids Python's banker's-rounding divergence between CPython builds
  and neutralises default-repr drift across magnitudes).
- ``SlotRef`` is expanded to ``{layer_id, texture_version, exclude_blend}``
  where ``texture_version`` is the referenced layer's monotonic version
  counter (pulled from the compose-time snapshot, NOT computed here).
- ``exclude_blend`` IS included in the slot hash — it represents an input
  semantics flag the backend may see. (Spec §2 noted it could be
  excluded as purely a UI concern; here we include it to be conservative:
  toggling the flag changes which pixels reach the Gateway in future
  implementations, so the same hash should NOT be reused.)

Seed handling (gallery_integration.md §2.3):
- ``seed=-1`` in ``param_values`` stays ``-1`` in the *pre-submit* hash
  (cache miss expected).
- After Gateway resolves the seed, the Controller should re-canonicalize
  with the resolved value before Gallery registration (see
  ``compute_param_hash`` docstring).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from loomstroke.plugins.ai_effect.data_model import SlotRef


def compute_param_hash(
    *,
    preset_id: str,
    gateway_file_hash: str,
    param_values: dict[str, Any],
    slot_refs: dict[str, SlotRef | None],
    layer_versions: dict[str, int],
) -> str:
    """Compute a deterministic sha256 over all inputs that affect generation.

    Args:
        preset_id: UUID of the preset driving this generation.
        gateway_file_hash: sha256 of the gateway_file.json content (precomputed
            by :func:`loomstroke.plugins.ai_effect.lsfx_io.read_lsfx` — cached
            on ``AIEffectPreset.gateway_file_hash`` and ``AIEffectState.gateway_file_hash``).
        param_values: internal_id → user-chosen value.
        slot_refs: internal_id → SlotRef (or None for empty required / optional).
        layer_versions: layer_id → texture_version. Every layer_id referenced
            by a non-None SlotRef MUST be present here. Missing → ``KeyError``.

    Returns:
        ``"sha256:{hex digest}"`` string.

    Raises:
        KeyError: if a slot's ``layer_id`` is absent from ``layer_versions``.
            This is a programmer error — callers are responsible for
            snapshotting referenced layer versions before calling.
    """
    payload = {
        "preset_id": preset_id,
        "gateway_file_hash": gateway_file_hash,
        "params": _canonicalize(param_values),
        "slots": _canonicalize_slots(slot_refs, layer_versions),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonicalize_slots(
    slot_refs: dict[str, SlotRef | None],
    layer_versions: dict[str, int],
) -> dict[str, Any]:
    """Expand SlotRef objects to a serializable, version-aware dict."""
    out: dict[str, Any] = {}
    for internal_id in sorted(slot_refs):
        ref = slot_refs[internal_id]
        if ref is None:
            out[internal_id] = None
        else:
            out[internal_id] = {
                "layer_id": ref.layer_id,
                "texture_version": layer_versions[ref.layer_id],
                "exclude_blend": ref.exclude_blend,
            }
    return out


def _canonicalize(value: Any) -> Any:
    """Recursively normalize values for stable JSON serialization.

    Floats are serialised as fixed-6-decimal strings (``f"{v:.6f}"``) rather
    than via ``round(v, 6)``. The latter uses Python's banker's-rounding on
    the underlying double, which can disagree between platforms and CPython
    builds for edge values (e.g. ``0.1234565``); the former is deterministic
    across every platform ``printf`` would run on.
    """
    # bool is a subclass of int — keep it out of the float branch AND
    # preserve its native JSON representation.
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, dict):
        return {k: _canonicalize(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    return value
