"""Auto write-back of ``est_runtime_ms`` into a ``.lsfx`` manifest.

Spec reference: ``AIeffect_design/04_python_design/preset_authoring.md`` §9
(Est. Runtime auto-measurement).

Policy (§9.2):
- Update ``manifest.json > execution.est_runtime_ms`` only when the current
  value is ``null`` (user-edited or already-measured values are preserved).
- Only touch ``.lsfx`` files inside ``user_data/ai_effect/presets/``
  (read-only bundled copies / external distribution packages are skipped).
- Use the same atomic-write path as ``write_lsfx_atomic`` so other fields
  (gateway_files, icon, preview) are preserved bit-for-bit.
- On any failure (file locked, read error, path policy violation) → log a
  warning and skip. Measurement is a best-effort convenience.
"""

from __future__ import annotations

import logging
from pathlib import Path

from loomstroke.plugins.ai_effect.lsfx_io import (
    LsfxReadError,
    read_lsfx,
    write_lsfx_atomic,
)

logger = logging.getLogger(__name__)


class EstRuntimeWritebackError(Exception):
    """Raised by ``apply_est_runtime_writeback`` on policy violations
    (path outside user preset dir, manifest already has a value, etc.).

    The typical caller (Phase 4-3 controller hook) catches this silently —
    the exception type exists so tests and opt-in diagnostics can branch.
    """


def apply_est_runtime_writeback(  # noqa: PLR0911
    *,
    lsfx_path: Path,
    measured_runtime_ms: int,
    user_presets_dir: Path,
) -> bool:
    """Write ``measured_runtime_ms`` into a preset's manifest.json iff
    the existing value is ``null`` and the preset is in the user dir.

    Args:
        lsfx_path: Absolute path to the ``.lsfx`` file.
        measured_runtime_ms: Non-negative milliseconds. Pass
            ``duration_sec * 1000`` from the Controller's success payload.
        user_presets_dir: The configured user presets directory. Writes are
            only performed when ``lsfx_path`` is inside this directory
            (preset_authoring.md §9.4).

    Returns:
        True if the manifest was updated on disk, False if skipped for any
        reason (non-user path, value already set, file missing, I/O error).
        Never raises — the caller can ignore the return value for
        fire-and-forget semantics.
    """
    if measured_runtime_ms < 0:
        logger.debug("Skipping runtime writeback: negative duration %s", measured_runtime_ms)
        return False

    try:
        if not _is_in_user_dir(lsfx_path, user_presets_dir):
            logger.debug(
                "Skipping runtime writeback: %s is not in user presets dir %s",
                lsfx_path,
                user_presets_dir,
            )
            return False
    except OSError as exc:
        logger.debug("Path resolution failed for %s: %s", lsfx_path, exc)
        return False

    if not lsfx_path.exists():
        logger.debug("Skipping runtime writeback: %s does not exist", lsfx_path)
        return False

    try:
        preset = read_lsfx(lsfx_path, source="user")
    except (LsfxReadError, FileNotFoundError, OSError) as exc:
        logger.warning("Could not read preset for runtime writeback: %s: %s", lsfx_path, exc)
        return False

    manifest = dict(preset.manifest)
    execution = dict(manifest.get("execution", {}))
    if not isinstance(execution, dict):
        logger.debug("Skipping runtime writeback: execution is not a dict in %s", lsfx_path)
        return False

    existing = execution.get("est_runtime_ms")
    if existing is not None:
        logger.debug(
            "Skipping runtime writeback: est_runtime_ms already set (%s) in %s",
            existing,
            lsfx_path,
        )
        return False

    execution["est_runtime_ms"] = int(measured_runtime_ms)
    manifest["execution"] = execution

    try:
        write_lsfx_atomic(
            lsfx_path,
            manifest,
            preset.gateway_files,
            icon_bytes=preset.icon_image,
            preview_bytes=preset.preview_image,
        )
    except (OSError, TypeError) as exc:
        logger.warning("Atomic write failed for runtime writeback on %s: %s", lsfx_path, exc)
        return False

    logger.info(
        "Wrote est_runtime_ms=%d into %s (first successful run)",
        measured_runtime_ms,
        lsfx_path,
    )
    return True


def _is_in_user_dir(candidate: Path, user_dir: Path) -> bool:
    """Return True if ``candidate`` resolves inside ``user_dir``.

    Uses ``Path.resolve(strict=False)`` so symlinks that cross the boundary
    are refused. Missing user_dir returns False (not an error).
    """
    try:
        resolved_candidate = candidate.resolve(strict=False)
        resolved_user = user_dir.resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    try:
        resolved_candidate.relative_to(resolved_user)
    except ValueError:
        return False
    return True
