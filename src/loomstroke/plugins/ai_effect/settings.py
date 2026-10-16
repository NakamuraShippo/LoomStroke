"""AI Effect preferences (preferences.json dataclass + atomic I/O).

Spec reference: ``AIeffect_design/04_python_design/settings.md`` §2.
Schema shape:

.. code-block:: json

   {
     "schema_version": 1,
     "defaults": {"trigger": "manual", "debounce_ms": 2000, "timeout_sec": 600},
     "cache": {"per_layer_cap": 20, "min_interval_ms": 500,
               "disable_cache_for_manual_regen": true},
     "retry": {"max_retries_on_change": 1, "max_retries_manual": 0,
               "retry_delay_ms": 2000},
     "concurrency": {"comfyui": 1, "nanobanana": 2, "gptimage2": 2, "openai": 2, "stability": 2},
     "logging": {"include_prompts": true, "include_param_values": true,
                 "max_file_mb": 50},
     "ui": {"show_first_gen_banner": true,
            "collapse_advanced_groups_default": true,
            "preview_throttle_ms": 500,
            "preset_manager_custom_categories": [],
            "preset_manager_custom_sources": [],
            "preset_manager_gateway_icons": {}},
     "developer": {"skip_onboarding": false, "force_gateway_id": null,
                   "log_level": "info"}
   }

Storage path: ``user_data/ai_effect/preferences.json`` (atomic write via
temp + ``os.replace``). Gateway-specific settings (URL/endpoint) live in
``gateways/{gateway_id}.json`` and are NOT handled here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loomstroke.plugins.ai_effect._atomic_io import atomic_write_json

logger = logging.getLogger(__name__)


CURRENT_SCHEMA_VERSION = 1


@dataclass
class DefaultsSection:
    trigger: str = "manual"
    debounce_ms: int = 2000
    timeout_sec: int = 600


@dataclass
class CacheSection:
    per_layer_cap: int = 20
    min_interval_ms: int = 500
    disable_cache_for_manual_regen: bool = True


@dataclass
class RetrySection:
    max_retries_on_change: int = 1
    max_retries_manual: int = 0
    retry_delay_ms: int = 2000


@dataclass
class ConcurrencySection:
    """Gateway-keyed concurrent-submit limits.

    Rationale for defaults: settings.md §2.1.1 — local ComfyUI is serial
    (VRAM contention), API gateways permit 2 by default.
    """

    comfyui: int = 1
    nanobanana: int = 2
    gptimage2: int = 2
    openai: int = 2
    stability: int = 2

    def get(self, gateway_id: str, default: int = 1) -> int:
        """Look up concurrency limit for a gateway, falling back to default."""
        return int(getattr(self, gateway_id, default))


@dataclass
class LoggingSection:
    include_prompts: bool = True
    include_param_values: bool = True
    max_file_mb: int = 50


@dataclass
class UISection:
    show_first_gen_banner: bool = True
    collapse_advanced_groups_default: bool = True
    preview_throttle_ms: int = 500
    preset_manager_custom_categories: list[str] = field(default_factory=list)
    preset_manager_custom_sources: list[str] = field(default_factory=list)
    preset_manager_gateway_icons: dict[str, str] = field(default_factory=dict)


@dataclass
class GallerySection:
    """Per-user policy on which generations land in the Gallery.

    Phase 6-i: ``on_change`` and ``debounced`` triggers can fire many
    intermediate generations while the user drags a slider or types a
    prompt — most of which are throwaway probes the user does not
    want cluttering history. ``register_auto_trigger_results`` defaults
    False so auto-fired runs stay out of the Gallery; flip to True to
    keep every generation regardless of trigger source.
    """

    register_auto_trigger_results: bool = False


@dataclass
class DeveloperSection:
    skip_onboarding: bool = False
    force_gateway_id: str | None = None
    log_level: str = "info"


@dataclass
class AIEffectPreferences:
    """Root preferences dataclass.

    Round-tripped to JSON via ``asdict(self)``. Loading applies migration
    if the stored ``schema_version`` differs from ``CURRENT_SCHEMA_VERSION``.
    """

    schema_version: int = CURRENT_SCHEMA_VERSION
    defaults: DefaultsSection = field(default_factory=DefaultsSection)
    cache: CacheSection = field(default_factory=CacheSection)
    retry: RetrySection = field(default_factory=RetrySection)
    concurrency: ConcurrencySection = field(default_factory=ConcurrencySection)
    logging: LoggingSection = field(default_factory=LoggingSection)
    ui: UISection = field(default_factory=UISection)
    gallery: GallerySection = field(default_factory=GallerySection)
    developer: DeveloperSection = field(default_factory=DeveloperSection)

    @classmethod
    def load(cls, path: Path) -> AIEffectPreferences:
        """Load preferences from JSON file, applying migration if needed.

        Missing file or corrupt JSON returns defaults (logs a warning). This
        matches settings.md §1.2 graceful-degradation policy.
        """
        if not path.exists():
            return cls()

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load AI Effect preferences from %s: %s", path, exc)
            return cls()

        migrated = migrate(raw)
        return _from_dict(migrated)

    def save(self, path: Path) -> None:
        """Atomic JSON write with fsync (see ``_atomic_io.atomic_write_json``)."""
        atomic_write_json(path, asdict(self))


# ================================================================
# Migration (settings.md §8)
# ================================================================


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply incremental migrations until ``schema_version`` matches current.

    Missing-field merge is the common case: new sections are filled with
    dataclass defaults. MAJOR-break migrations register explicit migrators
    below.
    """
    version = int(raw.get("schema_version", 0))
    while version < CURRENT_SCHEMA_VERSION:
        migrator = _MIGRATIONS.get(version)
        if migrator is None:
            # No migrator registered — bump version (additive change assumed).
            version += 1
            raw["schema_version"] = version
            continue
        raw = migrator(raw)
        version = int(raw["schema_version"])
    return raw


_MIGRATIONS: dict[int, Any] = {}


def _from_dict(raw: dict[str, Any]) -> AIEffectPreferences:
    """Construct ``AIEffectPreferences`` from a migrated dict.

    Unknown keys are silently dropped so forward-compatible reads
    (older client, newer file) don't crash.
    """
    return AIEffectPreferences(
        schema_version=int(raw.get("schema_version", CURRENT_SCHEMA_VERSION)),
        defaults=DefaultsSection(**_filter_keys(raw.get("defaults", {}), DefaultsSection)),
        cache=CacheSection(**_filter_keys(raw.get("cache", {}), CacheSection)),
        retry=RetrySection(**_filter_keys(raw.get("retry", {}), RetrySection)),
        concurrency=ConcurrencySection(
            **_filter_keys(raw.get("concurrency", {}), ConcurrencySection)
        ),
        logging=LoggingSection(**_filter_keys(raw.get("logging", {}), LoggingSection)),
        ui=UISection(**_filter_keys(raw.get("ui", {}), UISection)),
        gallery=GallerySection(**_filter_keys(raw.get("gallery", {}), GallerySection)),
        developer=DeveloperSection(**_filter_keys(raw.get("developer", {}), DeveloperSection)),
    )


def _filter_keys(data: dict[str, Any], dataclass_type: type) -> dict[str, Any]:
    """Drop keys not present in the target dataclass (forward compat)."""
    field_names = {f.name for f in dataclass_type.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return {k: v for k, v in data.items() if k in field_names}


def default_preferences_path() -> Path:
    """Return the canonical on-disk location of ``preferences.json``.

    Single source of truth so that the app-startup wiring
    (``loomstroke.app``) and the Preferences dialog tab
    (``loomstroke.ui.dialogs.preferences_dialog.AIEffectPage``) agree.
    Tests that want to override this location monkey-patch
    ``loomstroke.platform.user_dirs.default_user_root``.
    """
    # Lazy import so this module stays importable in contexts where the
    # platform sub-package hasn't been initialised yet (e.g. very early
    # test bootstraps).
    from loomstroke.platform.user_dirs import default_user_root  # noqa: PLC0415

    return default_user_root() / "ai_effect" / "preferences.json"
