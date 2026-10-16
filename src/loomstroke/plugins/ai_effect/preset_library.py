"""AIEffectPresetLibrary — in-memory registry of installed ``.lsfx`` presets.

Spec reference:
- ``AIeffect_design/04_python_design/data_model.md`` §3
- ``AIeffect_design/04_python_design/onboarding.md`` §4.3 (bundled copy-on-first-launch)
- ``AIeffect_design/04_python_design/onboarding.md`` §10 (user_data/ai_effect/ layout)

Storage layout (onboarding.md §10):

.. code-block:: text

    <user_data_root>/ai_effect/
    ├── presets/                 Installed .lsfx (user-modifiable)
    │   ├── {preset_id}.lsfx
    │   └── ...
    └── bundled/                 Optional snapshot of distribution-provided
                                  bundled presets, when present

The default LoomStroke build ships no ``.lsfx`` presets. If a downstream
distribution provides optional bundled files under
``src/loomstroke/plugins/ai_effect/bundled_presets/*.lsfx``,
``initialize_bundled`` copies missing presets into ``presets/`` and snapshots
them in ``bundled/`` so later upgrades can detect bundled changes vs user
modifications.
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from loomstroke.plugins.ai_effect.lsfx_io import LsfxReadError, read_lsfx
from loomstroke.plugins.ai_effect.manifest_validator import validate_manifest
from loomstroke.plugins.ai_effect.preset import AIEffectPreset

logger = logging.getLogger(__name__)


class PresetNotFoundError(LookupError):
    """Raised by ``AIEffectPresetLibrary.get`` when preset_id is not registered."""


class DuplicatePresetError(ValueError):
    """Raised when importing a preset whose preset_id already exists AND the
    caller did not opt into overwrite."""


class AIEffectPresetLibrary:
    """Registry of all installed AI Effect presets in the current process.

    Lifecycle:
    - ``__init__`` sets directory paths but does NOT scan disk. Call
      ``initialize_bundled()`` once at startup (no-op when no bundled files
      are provided), then ``scan_and_load()`` to populate the in-memory
      registry.
    - ``import_lsfx(source_path)`` copies an external .lsfx into the user
      presets dir and registers it.
    - ``delete(preset_id)`` removes the .lsfx file and unregisters.

    Thread safety: Qt main thread only (no internal locks). Phase 4
    controller calls from the main thread.
    """

    def __init__(
        self,
        *,
        user_presets_dir: Path,
        bundled_snapshot_dir: Path,
        bundled_source_dir: Path | None = None,
    ) -> None:
        """
        Args:
            user_presets_dir: Absolute path to user preset directory
                (e.g. ``<user_data>/ai_effect/presets/``). Created if missing.
            bundled_snapshot_dir: Absolute path for storing bundled snapshots
                (e.g. ``<user_data>/ai_effect/bundled/``). Used to detect
                bundled-vs-user diffs on upgrade.
            bundled_source_dir: Optional package-relative path to bundled
                presets (usually ``plugins/ai_effect/bundled_presets/``).
                None disables bundled initialization.
        """
        self._user_dir = Path(user_presets_dir)
        self._bundled_snapshot_dir = Path(bundled_snapshot_dir)
        self._bundled_source_dir = (
            Path(bundled_source_dir) if bundled_source_dir is not None else None
        )
        self._presets: dict[str, AIEffectPreset] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize_bundled(self) -> list[str]:
        """Copy missing bundled presets into user dir + refresh snapshot.

        Returns:
            List of preset file names newly copied. Empty list if
            ``bundled_source_dir`` is None, missing, or empty.

        Policy (onboarding.md §4.3):
        - Default builds usually have no bundled .lsfx, so this is a no-op.
        - If bundled .lsfx files are provided, each missing preset is copied
          into presets/
        - Later launches: preset_id collision → user's file is preserved,
          bundled file is NOT overwritten. A diff is logged so future
          UI work can prompt the user about version changes.
        - Snapshot in bundled/ is always refreshed so the next upgrade
          can compare against the current shipped version.
        """
        if self._bundled_source_dir is None or not self._bundled_source_dir.exists():
            logger.debug("Bundled source dir missing or disabled: %s", self._bundled_source_dir)
            return []

        self._user_dir.mkdir(parents=True, exist_ok=True)
        self._bundled_snapshot_dir.mkdir(parents=True, exist_ok=True)

        copied: list[str] = []
        # Build a quick lookup of user-installed preset_ids (tolerates missing)
        existing_ids = self._scan_preset_ids(self._user_dir)

        for lsfx_path in sorted(self._bundled_source_dir.glob("*.lsfx")):
            try:
                preset = read_lsfx(lsfx_path, source="builtin")
            except (LsfxReadError, FileNotFoundError, OSError) as exc:
                logger.warning("Skipping bundled preset %s due to load error: %s", lsfx_path, exc)
                continue

            # Snapshot bundled version (always refreshed)
            snapshot_target = self._bundled_snapshot_dir / lsfx_path.name
            try:
                shutil.copy2(lsfx_path, snapshot_target)
            except OSError as exc:
                logger.warning("Could not snapshot %s: %s", lsfx_path, exc)

            # Copy into user presets if not already installed
            if preset.preset_id in existing_ids:
                logger.debug(
                    "Bundled preset %s already installed as user preset; skipping copy",
                    preset.preset_id,
                )
                continue

            user_target = self._user_dir / lsfx_path.name
            try:
                shutil.copy2(lsfx_path, user_target)
                copied.append(lsfx_path.name)
            except OSError as exc:
                logger.warning("Failed to install bundled preset %s: %s", lsfx_path, exc)

        return copied

    def scan_and_load(self) -> int:
        """Load all ``.lsfx`` files from ``user_presets_dir`` into memory.

        Invalid / corrupt presets are skipped with a warning log so one bad
        preset never blocks the whole app. Returns the count of successfully
        loaded presets.
        """
        self._presets.clear()
        if not self._user_dir.exists():
            logger.debug("User presets dir does not exist: %s", self._user_dir)
            return 0

        for lsfx_path in sorted(self._user_dir.glob("*.lsfx")):
            try:
                preset = read_lsfx(lsfx_path, source="user")
            except (LsfxReadError, FileNotFoundError, OSError) as exc:
                logger.warning("Skipping invalid preset %s: %s", lsfx_path, exc)
                continue

            result = validate_manifest(preset.manifest)
            if not result.ok:
                logger.warning(
                    "Skipping preset %s: manifest validation failed (%s)",
                    lsfx_path,
                    result.reason,
                )
                continue

            if preset.preset_id in self._presets:
                existing = self._presets[preset.preset_id]
                logger.warning(
                    "Duplicate preset_id %s in %s (already loaded from %s); keeping first",
                    preset.preset_id,
                    lsfx_path,
                    existing.lsfx_path,
                )
                continue

            self._presets[preset.preset_id] = preset

        return len(self._presets)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, preset_id: str) -> AIEffectPreset:
        """Resolve preset_id → AIEffectPreset. Raises :class:`PresetNotFoundError` on miss."""
        try:
            return self._presets[preset_id]
        except KeyError as exc:
            raise PresetNotFoundError(
                f"Preset {preset_id!r} is not registered. " f"Known: {sorted(self._presets)}"
            ) from exc

    def list_all(self) -> list[AIEffectPreset]:
        """Return all presets in insertion order."""
        return list(self._presets.values())

    def list_by_category(self, category: str | None = None) -> list[AIEffectPreset]:
        """Filter presets by metadata.category (None = no filter)."""
        if category is None:
            return self.list_all()
        return [p for p in self._presets.values() if p.category == category]

    def list_by_gateway(self, gateway_id: str | None = None) -> list[AIEffectPreset]:
        """Filter presets by manifest.gateway (None = no filter)."""
        if gateway_id is None:
            return self.list_all()
        return [p for p in self._presets.values() if p.gateway_id == gateway_id]

    def __len__(self) -> int:
        return len(self._presets)

    def __contains__(self, preset_id: object) -> bool:
        return preset_id in self._presets

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def import_lsfx(
        self,
        source_path: Path | str,
        *,
        overwrite: bool = False,
    ) -> AIEffectPreset:
        """Copy an external ``.lsfx`` into the user presets dir and register it.

        Args:
            source_path: Path to a .lsfx anywhere on disk.
            overwrite: If True, a preset_id collision replaces the existing
                entry (the old file is preserved as
                ``{name}.bak-{timestamp}.lsfx``). If False, collision raises
                :class:`DuplicatePresetError`.

        Returns:
            The newly-registered AIEffectPreset.

        Raises:
            LsfxReadError: Source file unreadable or invalid.
            PresetValidationResult failure → LsfxReadError reraised
                with details (manifest rejected).
            DuplicatePresetError: Collision without ``overwrite=True``.
        """
        source = Path(source_path)
        preset = read_lsfx(source, source="imported")
        result = validate_manifest(preset.manifest)
        if not result.ok:
            raise LsfxReadError(
                f"Preset manifest invalid: reason={result.reason} detail={result.detail}"
            )

        existing = self._presets.get(preset.preset_id)
        if existing is not None and not overwrite:
            raise DuplicatePresetError(
                f"Preset {preset.preset_id!r} already installed "
                f"(existing={existing.lsfx_path}, new={source})"
            )

        self._user_dir.mkdir(parents=True, exist_ok=True)
        target = self._user_dir / source.name

        # Self-sync short-circuit (Phase 7-g): when the Editor has just written
        # a .lsfx directly into user_dir, ``source`` and ``target`` resolve to
        # the same file. Running the normal move-then-copy path would
        # (1) move the file we just wrote to a ``.bak-<ts>`` name and
        # (2) FileNotFoundError on the subsequent copy — destroying the
        # export. Skip the file ops entirely and just refresh the in-memory
        # entry from the file already at ``target``.
        if _paths_refer_to_same_file(source, target):
            preset = read_lsfx(target, source="user")
            self._presets[preset.preset_id] = preset
            return preset

        if (
            existing is not None
            and overwrite
            and existing.lsfx_path is not None
            and existing.lsfx_path.exists()
        ):
            # Back up the existing file before overwriting
            ts = _backup_timestamp()
            backup = existing.lsfx_path.with_name(
                f"{existing.lsfx_path.stem}.bak-{ts}{existing.lsfx_path.suffix}"
            )
            try:
                shutil.move(str(existing.lsfx_path), str(backup))
            except OSError as exc:
                logger.warning("Could not back up existing preset %s: %s", existing.lsfx_path, exc)

        shutil.copy2(source, target)
        # Re-read from the new location so lsfx_path reflects the installed path.
        preset = read_lsfx(target, source="user")
        self._presets[preset.preset_id] = preset
        return preset

    def delete(self, preset_id: str) -> bool:
        """Remove preset by id. Returns True if the file was removed.

        Builtin-source presets are NOT deletable (per data_model.md §3 and
        onboarding.md §4.3). Attempting to delete one returns False.
        """
        preset = self._presets.get(preset_id)
        if preset is None:
            return False
        if preset.source == "builtin":
            logger.warning("Refusing to delete builtin preset %s", preset_id)
            return False
        if preset.lsfx_path is not None and preset.lsfx_path.exists():
            try:
                preset.lsfx_path.unlink()
            except OSError as exc:
                logger.warning("Could not delete preset file %s: %s", preset.lsfx_path, exc)
                return False
        self._presets.pop(preset_id, None)
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _scan_preset_ids(directory: Path) -> set[str]:
        """Return the set of preset_ids already present in a directory.

        Invalid files are skipped silently; this is only used to decide
        whether to copy bundled files.
        """
        ids: set[str] = set()
        if not directory.exists():
            return ids
        for lsfx_path in directory.glob("*.lsfx"):
            try:
                preset = read_lsfx(lsfx_path, source="user")
            except (LsfxReadError, FileNotFoundError, OSError) as exc:
                logger.debug("scan_preset_ids: skipping %s: %s", lsfx_path, exc)
                continue
            if preset.preset_id:
                ids.add(preset.preset_id)
        return ids


def _backup_timestamp() -> str:
    """UTC timestamp suitable for backup file names (``20260424T103015Z``)."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _paths_refer_to_same_file(a: Path, b: Path) -> bool:
    """Return True when ``a`` and ``b`` identify the same file on disk.

    Uses :meth:`Path.resolve` so symlinks, ``..`` traversals, and case
    differences on case-insensitive filesystems all collapse to the same
    identity. Falls back to string equality when ``resolve`` raises
    (missing target on Windows with strict=True is not an issue in
    Python 3.11 default, but OSError can still surface for broken
    symlinks).
    """
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)
