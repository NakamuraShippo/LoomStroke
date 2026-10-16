"""AIEffectPreset — in-memory representation of an installed ``.lsfx`` preset.

Spec reference: ``AIeffect_design/04_python_design/data_model.md`` §3.

Distinct from :class:`loomstroke.plugins.ai_effect.data_model.AIEffectState`:
- ``AIEffectPreset`` is what sits in :class:`AIEffectPresetLibrary` (Phase 3-3).
  One instance per installed ``.lsfx`` file.
- ``AIEffectState`` is what a specific ``_Layer`` carries at runtime (a
  *snapshot* of one preset + user's live parameter values).

Preset updates never mutate already-attached ``AIEffectState`` instances;
that decoupling is enforced at attach time by deep-copying manifest +
gateway_file into the state (see data_model.md §3 / intro).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AIEffectPreset:
    """One installed ``.lsfx`` preset entry (library-scoped).

    Attributes:
        preset_id: UUID from manifest.json.
        manifest: Parsed manifest.json content.
        gateway_files: Mapping of gateway_id → gateway file dict.
            Typically has exactly one entry (the manifest's ``gateway`` key).
            Multiple entries are reserved for future "same effect on multiple
            backends" support.
        gateway_file_hash: sha256 of the primary gateway file (the one
            matching manifest['gateway']). Cached at load time so param_hash
            computations in runtime don't re-hash on every keystroke.
        source: ``"builtin"`` (shipped with LoomStroke) / ``"user"``
            (authored locally) / ``"imported"`` (dropped in by user from an
            external .lsfx).
        lsfx_path: Absolute path to the .lsfx file on disk. May be None for
            builtin presets that are loaded from a packaged resource.
        icon_image: Raw bytes of ``icon.png`` if embedded. None otherwise.
        preview_image: Raw bytes of ``preview.png`` if embedded.
    """

    preset_id: str
    manifest: dict[str, Any]
    gateway_files: dict[str, dict[str, Any]]
    gateway_file_hash: str = ""
    source: str = "user"
    lsfx_path: Path | None = None
    icon_image: bytes | None = None
    preview_image: bytes | None = None
    # --- Derived convenience fields, populated from manifest ---
    name: str = ""
    category: str = ""
    gateway_id: str = ""

    def __post_init__(self) -> None:
        """Populate derived fields from manifest (read-only convenience)."""
        metadata = self.manifest.get("metadata", {}) if isinstance(self.manifest, dict) else {}
        self.name = str(metadata.get("name", "")) if isinstance(metadata, dict) else ""
        self.category = str(metadata.get("category", "")) if isinstance(metadata, dict) else ""
        self.gateway_id = (
            str(self.manifest.get("gateway", "")) if isinstance(self.manifest, dict) else ""
        )

    def primary_gateway_file(self) -> dict[str, Any] | None:
        """Return the gateway file matching ``manifest['gateway']``."""
        return self.gateway_files.get(self.gateway_id)


@dataclass
class PresetValidationResult:
    """Result of ``validate_manifest`` / gateway_file validation.

    ``ok=True`` with non-empty ``warnings`` is acceptable for graceful
    degradation cases (unknown trigger value, unknown category, etc.).
    ``ok=False`` blocks the preset from being installed / executed.
    """

    ok: bool
    reason: str | None = None
    detail: str | None = None
    warnings: list[str] = field(default_factory=list)
