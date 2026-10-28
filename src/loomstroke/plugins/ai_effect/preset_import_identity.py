"""Resolve import UUID/file ownership without changing the library or its files."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from loomstroke.plugins.ai_effect.lsfx_io import LsfxReadError, read_lsfx
from loomstroke.plugins.ai_effect.manifest_validator import validate_manifest
from loomstroke.plugins.ai_effect.preset import AIEffectPreset

logger = logging.getLogger(__name__)
_BACKUP_NAME = re.compile(r".+\.bak-[0-9]{8}T[0-9]{6}Z\.lsfx", re.IGNORECASE)


def is_generated_backup(path: Path) -> bool:
    return _BACKUP_NAME.fullmatch(path.name) is not None


def ordered_preset_paths(directory: Path) -> list[Path]:
    """Prefer normal presets; keep valid backups available as UUID fallbacks."""
    return sorted(directory.glob("*.lsfx"), key=lambda path: (is_generated_backup(path), path))


def read_validated_preset(path: Path, *, source: str = "user") -> AIEffectPreset:
    preset = read_lsfx(path, source=source)
    result = validate_manifest(preset.manifest)
    if not result.ok:
        raise LsfxReadError(
            f"Preset manifest invalid: reason={result.reason} detail={result.detail}"
        )
    return preset


def inspect_import_target(target: Path) -> AIEffectPreset | None:
    """An occupied but unreadable target cannot be treated as free space."""
    try:
        target.lstat()
    except FileNotFoundError:
        return None
    return read_validated_preset(target)


def find_installed_uuid(
    presets: dict[str, AIEffectPreset],
    directory: Path,
    preset_id: str,
    target: AIEffectPreset | None,
) -> AIEffectPreset | None:
    """Find a real UUID owner, including files not yet registered in memory.

    A cached index is a path hint, not permission to move a file whose actual
    UUID has changed. Unrelated corrupt files retain the scan's tolerant policy.
    The library and cold lookup share the normal-file-before-backup order.
    """
    if target is not None:
        return target  # The caller has already checked the target UUID.

    checked: set[Path] = set()
    indexed = presets.get(preset_id)
    if indexed is not None and indexed.lsfx_path is not None:
        path = indexed.lsfx_path
        checked.add(path)
        try:
            actual = read_validated_preset(path)
        except FileNotFoundError:
            pass
        else:
            if actual.preset_id == preset_id:
                return actual

    for path in ordered_preset_paths(directory):
        if path in checked:
            continue
        try:
            actual = read_validated_preset(path)
        except (LsfxReadError, OSError):
            logger.debug("Skipping unrelated invalid preset during import inspection: %s", path)
            continue
        if actual.preset_id == preset_id:
            return actual
    return None


def unique_normal_uuid_owner(directory: Path, preset_id: str) -> AIEffectPreset | None:
    """Resolve one ordinary owner for checked retirement; do not choose among duplicates."""
    owner: AIEffectPreset | None = None
    for path in ordered_preset_paths(directory):
        if is_generated_backup(path):
            continue
        try:
            candidate = read_validated_preset(path)
        except (LsfxReadError, OSError):
            continue  # Preserve tolerant handling of unrelated invalid archives.
        if candidate.preset_id != preset_id:
            continue
        if owner is not None:
            raise LsfxReadError("Preset UUID has multiple normal file owners")
        owner = candidate
    return owner


def refreshed_self_sync_index(
    presets: dict[str, AIEffectPreset], preset: AIEffectPreset, target: Path
) -> dict[str, AIEffectPreset]:
    """Publish a validated file's current UUID and drop only its stale aliases."""
    candidate = {
        preset_id: existing
        for preset_id, existing in presets.items()
        if existing.lsfx_path is None or not paths_refer_to_same_file(existing.lsfx_path, target)
    }
    candidate[preset.preset_id] = preset
    return candidate


def paths_refer_to_same_file(a: Path, b: Path) -> bool:
    """Keep the existing resolved-path comparison (not a hardlink identity test)."""
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)
