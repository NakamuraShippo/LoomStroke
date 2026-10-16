"""Preset import pipeline (Phase 7-f).

Counterpart to ``export.py``. Takes an installed or external ``.lsfx`` and
reconstructs the editor state the author would have been viewing just
before they ran Export: raw workflow, ``ExportMetadata``, ``AuthorChoices``
(merging manifest + gateway_file.node_mappings), and the original
``preset_id`` so re-exporting preserves identity.

Design choices:
- Takes a :class:`GatewayRegistry` (not a resolved :class:`AIGateway`) so
  the caller can auto-switch the UI gateway combo based on the loaded
  ``manifest['gateway']``. Validation whether the required gateway is
  registered happens here, before any state mutation on the caller side.
- Does NOT call ``gateway.parse_author_choices``: some adapters implement
  that path but it can be intentionally lossy (display_name, type,
  default, mode, group are fabricated). The manifest already carries all
  those fields faithfully, so we read them directly and merge
  ``gateway_ref`` in from the gateway file's mapping table.
- ``ExportMetadata.icon_bytes`` is populated from the embedded ``icon.png``
  so the import→edit→export round-trip preserves the icon without the
  author re-picking it from disk.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loomstroke.plugins.ai_effect.editor.metadata_form import ExportMetadata
from loomstroke.plugins.ai_effect.gateway_protocol import (
    AuthorChoices,
    GroupChoice,
    OutputChoice,
    ParamChoice,
    SlotChoice,
)
from loomstroke.plugins.ai_effect.lsfx_io import LsfxReadError, read_lsfx
from loomstroke.plugins.ai_effect.manifest_validator import validate_manifest
from loomstroke.plugins.ai_effect.registry import GatewayRegistry, UnknownGatewayError

logger = logging.getLogger(__name__)


class ImportPresetError(ValueError):
    """Raised when a ``.lsfx`` cannot be loaded for editing.

    Caught by :class:`EffectEditorWindow` and surfaced in the status bar.
    Reason strings follow the ``"<category>:<detail>"`` convention used by
    ``ExportValidationError`` so the host's format helper can dispatch
    messages uniformly.
    """


@dataclass
class ImportedPresetState:
    """Snapshot of an imported ``.lsfx`` ready to populate the editor.

    ``source_path`` is the archive the state was loaded from — the editor
    uses it as the default directory for re-export so overwriting the
    source file is a one-click operation.

    ``endpoint_url`` carries the optional per-preset endpoint URL the
    Phase 8a-bridge ComfyUI flow stores in ``gateway_file``. The editor
    populates its URL input from this field on import; gateways that
    do not use endpoint_url (NanoBanana) leave it ``None``.

    ``target_server_id`` carries the optional Phase 10c-ui server id
    (``"local"`` / ``"external"`` / a custom registered id). When
    present, the editor's server picker reflects this selection on
    import so the round-trip preserves the author's Settings-bound
    server choice. ``endpoint_url`` and ``target_server_id`` are
    independent — a preset stores at most one of them.
    """

    preset_id: str
    gateway_id: str
    source_path: Path
    raw_workflow: dict[str, Any]
    metadata: ExportMetadata
    choices: AuthorChoices
    endpoint_url: str | None = None
    target_server_id: str | None = None
    native_workflow: dict[str, Any] | None = None


def load_preset_for_editing(
    path: Path | str,
    registry: GatewayRegistry,
) -> ImportedPresetState:
    """Read a ``.lsfx`` and rebuild an :class:`ImportedPresetState`.

    Failure modes (reason prefix shown for host translation):
    - ``read_error:<detail>`` — archive missing, corrupt zip, or I/O error
    - ``manifest_invalid:<validator_reason>`` — manifest fails validator
    - ``gateway_not_registered:<id>`` — manifest's gateway has no registered
      implementation in this LoomStroke install
    - ``gateway_file_missing:<id>`` — defensive; ``read_lsfx`` already
      rejects this, but keeps the caller honest if the invariant drifts
    - ``no_raw_workflow`` — gateway's ``extract_raw_workflow`` returned
      None for a workflow-backed gateway
    """
    archive_path = Path(path)
    try:
        preset = read_lsfx(archive_path, source="imported")
    except (LsfxReadError, FileNotFoundError, OSError) as exc:
        raise ImportPresetError(f"read_error:{exc}") from exc

    result = validate_manifest(preset.manifest)
    if not result.ok:
        raise ImportPresetError(f"manifest_invalid:{result.reason}")

    gateway_id = preset.gateway_id
    try:
        gateway = registry.get(gateway_id)
    except UnknownGatewayError as exc:
        raise ImportPresetError(f"gateway_not_registered:{gateway_id}") from exc

    gateway_file = preset.primary_gateway_file()
    if gateway_file is None:
        raise ImportPresetError(f"gateway_file_missing:{gateway_id}")

    raw_workflow = gateway.extract_raw_workflow(gateway_file)
    if raw_workflow is None:
        if _is_param_mapping_gateway_file(gateway_file):
            raw_workflow = {}
        else:
            raise ImportPresetError("no_raw_workflow")

    metadata = _build_metadata(preset.manifest, preset.icon_image, preset.preview_image)
    choices = _build_author_choices(preset.manifest, gateway_file)

    logger.info(
        "Loaded preset %s (gateway=%s) from %s for editing",
        preset.preset_id,
        gateway_id,
        archive_path,
    )
    raw_endpoint = gateway_file.get("endpoint_url")
    endpoint_url = raw_endpoint if isinstance(raw_endpoint, str) and raw_endpoint else None
    raw_server_id = gateway_file.get("target_server_id")
    target_server_id = raw_server_id if isinstance(raw_server_id, str) and raw_server_id else None
    return ImportedPresetState(
        preset_id=preset.preset_id,
        gateway_id=gateway_id,
        source_path=archive_path,
        raw_workflow=raw_workflow,
        metadata=metadata,
        choices=choices,
        endpoint_url=endpoint_url,
        target_server_id=target_server_id,
        native_workflow=gateway_file.get("native_workflow"),
    )


# ---------------------------------------------------------------------------
# Manifest → editor-state helpers
# ---------------------------------------------------------------------------


def _build_metadata(
    manifest: dict[str, Any],
    icon_bytes: bytes | None,
    preview_bytes: bytes | None = None,
) -> ExportMetadata:
    md = manifest.get("metadata")
    if not isinstance(md, dict):
        md = {}
    execution = manifest.get("execution")
    trigger_value = execution.get("trigger") if isinstance(execution, dict) else None
    tags_value = md.get("tags")
    if isinstance(tags_value, list):
        tag_list = [str(t) for t in tags_value if isinstance(t, str) and t]
    else:
        tag_list = []
    return ExportMetadata(
        name=str(md.get("name", "")),
        category=str(md.get("category", "other")) if md.get("category") is not None else "other",
        description=str(md.get("description", "")) if md.get("description") is not None else "",
        tags=tag_list,
        trigger=str(trigger_value) if isinstance(trigger_value, str) else "manual",
        icon_bytes=icon_bytes,
        preview_bytes=preview_bytes,
    )


def _choice_mappings(gateway_file: dict[str, Any]) -> dict[str, Any]:
    """Return the gateway-file mapping table used to rebuild gateway_ref.

    ComfyUI stores author bindings in ``node_mappings``. NanoBanana has no
    workflow graph and stores the same internal_id -> gateway_ref relationship
    in ``param_mappings``. The editor's manifest restoration is otherwise
    gateway-agnostic, so both tables can feed the same merge path.
    """
    mappings = gateway_file.get("node_mappings")
    if isinstance(mappings, dict):
        return mappings
    mappings = gateway_file.get("param_mappings")
    if isinstance(mappings, dict):
        return mappings
    return {}


def _is_param_mapping_gateway_file(gateway_file: dict[str, Any]) -> bool:
    """Return True for workflow-less gateway files such as NanoBanana."""
    return isinstance(gateway_file.get("param_mappings"), dict)


def _mapping_ref(mappings: Any, internal_id: str) -> dict[str, Any]:
    """Look up ``internal_id`` in a mapping table and return a fresh dict.

    Returns an empty dict when the mapping is absent or not a dict —
    keeps the resulting ``gateway_ref`` well-typed for downstream code.
    The author loses backend linkage for that candidate on re-export,
    but the in-editor state is consistent.
    """
    if not isinstance(mappings, dict):
        return {}
    raw = mappings.get(internal_id)
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def _build_author_choices(
    manifest: dict[str, Any],
    gateway_file: dict[str, Any],
) -> AuthorChoices:
    mappings = _choice_mappings(gateway_file)

    slots: list[SlotChoice] = []
    for entry in manifest.get("input_slots", []):
        if not isinstance(entry, dict):
            continue
        internal_id = str(entry.get("internal_id", ""))
        if not internal_id:
            continue
        slots.append(
            SlotChoice(
                internal_id=internal_id,
                display_name=str(entry.get("display_name", internal_id)),
                required=bool(entry.get("required", False)),
                order=_coerce_order(entry.get("order")),
                gateway_ref=_mapping_ref(mappings, internal_id),
            )
        )

    params: list[ParamChoice] = []
    for entry in manifest.get("parameters", []):
        if not isinstance(entry, dict):
            continue
        internal_id = str(entry.get("internal_id", ""))
        if not internal_id:
            continue
        rng = entry.get("range")
        rng_dict = dict(rng) if isinstance(rng, dict) else None
        choices_raw = entry.get("choices")
        choices_list = list(choices_raw) if isinstance(choices_raw, list) else None
        group_raw = entry.get("group")
        group = group_raw if isinstance(group_raw, str) and group_raw else None
        params.append(
            ParamChoice(
                internal_id=internal_id,
                display_name=str(entry.get("display_name", internal_id)),
                default=entry.get("default"),
                group=group,
                type=str(entry.get("type", "string")),
                range=rng_dict,
                choices=choices_list,
                required_text=bool(entry.get("required_text", False)),
                order=_coerce_order(entry.get("order")),
                gateway_ref=_mapping_ref(mappings, internal_id),
            )
        )

    outputs: list[OutputChoice] = []
    for entry in manifest.get("outputs", []):
        if not isinstance(entry, dict):
            continue
        internal_id = str(entry.get("internal_id", ""))
        if not internal_id:
            continue
        outputs.append(
            OutputChoice(
                internal_id=internal_id,
                display_name=str(entry.get("display_name", internal_id)),
                mode=str(entry.get("mode", "new_layer_above")),
                overwrite_on_regenerate=bool(entry.get("overwrite_on_regenerate", True)),
                order=_coerce_order(entry.get("order")),
                gateway_ref=_mapping_ref(mappings, internal_id),
            )
        )

    groups: list[GroupChoice] = []
    for g in manifest.get("parameter_groups", []):
        if not isinstance(g, dict):
            continue
        name = g.get("name")
        if not isinstance(name, str) or not name:
            continue
        groups.append(
            GroupChoice(
                name=name,
                order=_coerce_order(g.get("order")),
                collapsed=bool(g.get("collapsed", False)),
            )
        )

    return AuthorChoices(
        input_slots=slots,
        parameters=params,
        outputs=outputs,
        parameter_groups=groups,
    )


def _coerce_order(value: Any) -> int:
    """Coerce a manifest ``order`` field to int.

    ``build_manifest`` always writes int, but presets edited by external
    tooling or older LoomStroke versions might carry floats / strings.
    Fall back to 0 rather than raising so a single bad order field does
    not block the whole import.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "ImportPresetError",
    "ImportedPresetState",
    "load_preset_for_editing",
]
