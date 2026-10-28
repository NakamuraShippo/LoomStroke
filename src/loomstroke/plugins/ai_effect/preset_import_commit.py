"""Validate normal-name preset candidates before their final file/index commit.

Same-path installs preserve the old canonical while preparing a separate backup.
Checked different-name retirement retains a verified backup through compensation.
Observed fingerprints do not provide native exclusion against another process.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

from loomstroke.plugins.ai_effect import preset_import_identity as identity
from loomstroke.plugins.ai_effect._atomic_io import atomic_write_binary
from loomstroke.plugins.ai_effect.lsfx_io import LsfxReadError
from loomstroke.plugins.ai_effect.preset import AIEffectPreset

_Fingerprint = tuple[int, int, int, int]


def _fingerprint(path: Path) -> _Fingerprint | None:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _require_unchanged(path: Path, expected: _Fingerprint | None) -> None:
    if _fingerprint(path) != expected:
        raise LsfxReadError("Preset import file changed during preparation")


def _require_absent(path: Path) -> None:
    if _fingerprint(path) is not None:
        raise FileExistsError("Preset import destination is already occupied")


def _json_identity(value: object) -> str:
    # Compare the reader's JSON values without conflating bools and numbers.
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _read_matching(path: Path, expected: AIEffectPreset) -> AIEffectPreset:
    actual = identity.read_validated_preset(path)
    if (
        _json_identity(actual.manifest) != _json_identity(expected.manifest)
        or _json_identity(actual.gateway_files) != _json_identity(expected.gateway_files)
        or actual.icon_image != expected.icon_image
        or actual.preview_image != expected.preview_image
    ):
        raise LsfxReadError("Preset import candidate differs from the validated preset")
    return actual


def commit_regular_import(
    source: Path,
    target: Path,
    incoming: AIEffectPreset,
    existing: AIEffectPreset | None,
    presets: dict[str, AIEffectPreset],
    *,
    backup_timestamp: str | None,
) -> tuple[AIEffectPreset, dict[str, AIEffectPreset]]:
    """Copy/validate, preserve old content, then publish the file before its index.

    This helper is used only for an absent target or its same-path UUID owner.
    Returned model/index are prepared while the candidate is still a stage, so
    the caller only assigns the index after the final atomic write succeeds.
    Existing backups are never deleted or implicitly replaced.
    """
    source_version = _fingerprint(source)
    _read_matching(source, incoming)
    _require_unchanged(source, source_version)

    target_version = _fingerprint(target)
    backup: Path | None = None
    if existing is None:
        _require_absent(target)
    else:
        _read_matching(target, existing)
        _require_unchanged(target, target_version)
        if backup_timestamp is None:
            raise ValueError("Preset import backup timestamp is required")
        backup = target.with_name(f"{target.stem}.bak-{backup_timestamp}{target.suffix}")
        _require_absent(backup)

    prepared: tuple[AIEffectPreset, dict[str, AIEffectPreset]] | None = None

    def _write_candidate(stage: Path) -> None:
        nonlocal prepared
        shutil.copy2(source, stage)
        candidate = _read_matching(stage, incoming)
        installed = dataclasses.replace(candidate, lsfx_path=target, source="user")
        candidate_index = identity.refreshed_self_sync_index(presets, installed, target)
        prepared = installed, candidate_index
        _require_unchanged(source, source_version)
        _require_unchanged(target, target_version)
        if backup is not None and existing is not None:
            _write_backup(target, backup, existing, target_version)
        _require_unchanged(source, source_version)
        _require_unchanged(target, target_version)

    atomic_write_binary(target, _write_candidate)
    if prepared is None:
        raise RuntimeError("Preset import candidate was not prepared")
    return prepared


def _write_backup(
    target: Path, backup: Path, existing: AIEffectPreset, target_version: _Fingerprint | None
) -> None:
    _require_absent(backup)

    def _copy_old(stage: Path) -> None:
        _require_unchanged(target, target_version)
        shutil.copy2(target, stage)
        _read_matching(stage, existing)
        _require_unchanged(target, target_version)
        _require_absent(backup)

    atomic_write_binary(backup, _copy_old)


class _PresetImportRescueError(LsfxReadError):
    """The original canonical could not be restored; its verified backup is usable."""

    def __init__(self, rescue_index: dict[str, AIEffectPreset]) -> None:
        super().__init__("Preset import failed; the previous preset is retained in its backup")
        self.rescue_index = rescue_index


@dataclasses.dataclass
class _RetirementReceipt:
    source: Path
    target: Path
    old: Path
    backup: Path
    incoming: AIEffectPreset
    existing: AIEffectPreset
    presets: dict[str, AIEffectPreset]
    source_version: _Fingerprint | None
    old_version: _Fingerprint | None
    stage_version: _Fingerprint | None = None
    restore_version: _Fingerprint | None = None
    prepared: tuple[AIEffectPreset, dict[str, AIEffectPreset]] | None = None
    rescue_index: dict[str, AIEffectPreset] | None = None

    def _normal_owner_is(self, expected: Path | None) -> bool:
        owner = identity.unique_normal_uuid_owner(self.target.parent, self.incoming.preset_id)
        if expected is None:
            return owner is None
        return (
            owner is not None
            and owner.lsfx_path is not None
            and identity.paths_refer_to_same_file(owner.lsfx_path, expected)
        )

    def _require_initial(self) -> None:
        _require_unchanged(self.source, self.source_version)
        _require_unchanged(self.old, self.old_version)
        _require_absent(self.target)
        _require_absent(self.backup)
        if not self._normal_owner_is(self.old):
            raise LsfxReadError("Preset UUID owner changed during preparation")

    def _backup_matches(self) -> bool:
        if _fingerprint(self.backup) != self.old_version:
            return False
        _read_matching(self.backup, self.existing)
        return True

    def _retired_matches(self) -> bool:
        try:
            return (
                _fingerprint(self.old) is None
                and _fingerprint(self.target) is None
                and self._backup_matches()
                and self._normal_owner_is(None)
            )
        except (LsfxReadError, OSError):
            return False

    def committed_matches(self) -> bool:
        try:
            if (
                self.stage_version is None
                or _fingerprint(self.target) != self.stage_version
                or _fingerprint(self.old) is not None
                or not self._backup_matches()
                or not self._normal_owner_is(self.target)
            ):
                return False
            _read_matching(self.target, self.incoming)
            return True
        except (LsfxReadError, OSError):
            return False

    def prepare(self, stage: Path) -> None:
        shutil.copy2(self.source, stage)
        candidate = _read_matching(stage, self.incoming)
        installed = dataclasses.replace(candidate, lsfx_path=self.target, source="user")
        rescue = dataclasses.replace(self.existing, lsfx_path=self.backup, source="user")
        candidate_index = identity.refreshed_self_sync_index(self.presets, installed, self.target)
        self.rescue_index = identity.refreshed_self_sync_index(self.presets, rescue, self.old)
        self.prepared = installed, candidate_index
        self.stage_version = _fingerprint(stage)
        if self.stage_version is None:
            raise LsfxReadError("Preset import candidate disappeared during preparation")
        self._require_initial()
        try:
            shutil.move(str(self.old), str(self.backup))
        except OSError:
            if not self._retired_matches():
                raise
        if not self._retired_matches():
            raise LsfxReadError("Preset retirement could not be confirmed")
        _require_unchanged(self.source, self.source_version)
        _require_unchanged(stage, self.stage_version)

    def _restored_matches(self) -> bool:
        try:
            if (
                self.restore_version is None
                or _fingerprint(self.old) != self.restore_version
                or _fingerprint(self.target) is not None
                or not self._backup_matches()
                or not self._normal_owner_is(self.old)
            ):
                return False
            _read_matching(self.old, self.existing)
            return True
        except (LsfxReadError, OSError):
            return False

    def restore_old(self) -> None:
        def _copy_backup(stage: Path) -> None:
            if not self._retired_matches():
                raise LsfxReadError("Preset retirement changed before restoration")
            shutil.copy2(self.backup, stage)
            _read_matching(stage, self.existing)
            self.restore_version = _fingerprint(stage)
            if self.restore_version is None or not self._retired_matches():
                raise LsfxReadError("Preset retirement changed during restoration")

        try:
            atomic_write_binary(self.old, _copy_backup, _temp_suffix=".tmp")
        except Exception:
            if self._restored_matches():
                return  # The restore rename completed before its error was reported.
            if self._retired_matches() and self.rescue_index is not None:
                raise _PresetImportRescueError(self.rescue_index) from None
            raise LsfxReadError("Preset import restoration could not be confirmed") from None
        if not self._restored_matches():
            if self._retired_matches() and self.rescue_index is not None:
                raise _PresetImportRescueError(self.rescue_index)
            raise LsfxReadError("Preset import restoration could not be confirmed")


def commit_retiring_import(
    source: Path,
    target: Path,
    incoming: AIEffectPreset,
    existing: AIEffectPreset,
    presets: dict[str, AIEffectPreset],
    *,
    backup_timestamp: str,
) -> tuple[AIEffectPreset, dict[str, AIEffectPreset]]:
    """Retire one ordinary owner only after the new candidate and indexes validate.

    Normal completion retains the legacy one-copy/one-move contract. Failure
    compensation may copy the backup back to the former canonical. Backups and
    foreign files are never deleted; two paths cannot be committed atomically.
    """
    old = existing.lsfx_path
    if old is None or identity.paths_refer_to_same_file(source, old):
        raise LsfxReadError("Preset retirement source is not independent of its old owner")
    receipt = _RetirementReceipt(
        source,
        target,
        old,
        old.with_name(f"{old.stem}.bak-{backup_timestamp}{old.suffix}"),
        incoming,
        existing,
        presets,
        _fingerprint(source),
        _fingerprint(old),
    )
    _read_matching(source, incoming)
    _read_matching(old, existing)
    receipt._require_initial()
    try:
        atomic_write_binary(target, receipt.prepare, _temp_suffix=".tmp")
        if not receipt.committed_matches():
            raise LsfxReadError("Preset import final replacement could not be confirmed")
    except Exception:
        if receipt.prepared is not None and receipt.committed_matches():
            return receipt.prepared
        if receipt._retired_matches():
            receipt.restore_old()
        raise
    if receipt.prepared is None:
        raise RuntimeError("Preset import candidate was not prepared")
    return receipt.prepared
