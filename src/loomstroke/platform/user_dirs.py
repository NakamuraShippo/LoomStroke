"""Helpers for resolving user-writable directories.

Per Master.md, configuration and layout artifacts should live under a
`LoomStroke/User/` tree inside the platform-specific application data
location.  This module centralises that logic so UI and core components
can agree on the same paths.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QStandardPaths

__all__ = [
    "default_layout_directory",
    "default_user_root",
    "ensure_directory",
    "plugin_data_dir",
    "plugin_data_root",
]

logger = logging.getLogger(__name__)

_LEGACY_APP_NAME = "SketchRush"
_CURRENT_APP_NAME = "LoomStroke"


def _legacy_counterpart(path: Path) -> Path | None:
    """Return the SketchRush-named equivalent of ``path``, or None if unchanged."""

    parts = list(path.parts)
    replaced = False
    for idx, part in enumerate(parts):
        if part == _CURRENT_APP_NAME:
            parts[idx] = _LEGACY_APP_NAME
            replaced = True
    return Path(*parts) if replaced else None


def _migrate_legacy_user_data(new_root: Path) -> None:
    """Rename legacy SketchRush user data to LoomStroke once if still present.

    Only applies when the new LoomStroke-named path does not yet exist and a
    corresponding SketchRush-named path does. Failures are logged and ignored
    so app startup is not blocked.
    """

    if new_root.exists():
        return
    legacy_root = _legacy_counterpart(new_root)
    if legacy_root is None or not legacy_root.exists():
        return
    try:
        new_root.parent.mkdir(parents=True, exist_ok=True)
        legacy_root.rename(new_root)
    except OSError as exc:
        logger.warning(
            "Failed to migrate legacy user data %s -> %s: %s",
            legacy_root,
            new_root,
            exc,
        )
        return
    logger.info("Migrated legacy user data: %s -> %s", legacy_root, new_root)


def _resolve_base_directory() -> Path:
    """Return the writable base directory for user data."""

    for location in (
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation),
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.HomeLocation),
        os.getcwd(),
    ):
        if location:
            return Path(location)
    return Path(os.getcwd())


def default_user_root(*, create: bool = True) -> Path:
    """Return the `LoomStroke/User` root folder, creating it when required."""

    base = _resolve_base_directory()
    repo_root = Path(__file__).resolve().parents[3]

    # Prefer repository-local user_data to keep distributions self-contained
    repo_user_root = repo_root / "user_data"

    # QStandardPaths(AppDataLocation) already includes organization/app on Windows
    # (e.g., .../LoomStroke/LoomStroke). Prefer the parent to avoid duplication.
    if base.name.lower() == "loomstroke" and base.parent.name.lower() == "loomstroke":
        appdata_user = base.parent / "User"
    elif base.name.lower() == "loomstroke":
        appdata_user = base / "User"
    else:
        appdata_user = base / "LoomStroke" / "User"

    candidates = [
        repo_user_root,
        appdata_user,
        Path.home() / "LoomStroke" / "User",
        Path(tempfile.gettempdir()) / "LoomStroke" / "User",
    ]

    for idx, root in enumerate(candidates):
        if not create:
            return root
        if idx > 0:
            _migrate_legacy_user_data(root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            if idx > 0:
                logger.warning("Falling back to user-writable layout directory: %s", root)
            return root
        except PermissionError:
            logger.warning("Layout directory not writable: %s", root)
            continue

    # As a last resort, return the first candidate without ensuring creation
    return candidates[0]


def default_layout_directory(*, create: bool = True) -> Path:
    """Return the default directory used for UI layout persistence."""

    return default_user_root(create=create)


def plugin_data_root(*, create: bool = True) -> Path:
    """Return the user_data root used for external plugin installation."""

    root = default_user_root(create=create)
    plugin_root = root / "plugins"
    if create:
        plugin_root.mkdir(parents=True, exist_ok=True)
    return plugin_root


def ensure_directory(path: str | Path) -> Path:
    """Ensure the provided path exists and return it as a :class:`Path`."""

    directory = Path(path).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def plugin_data_dir(plugin_id: str, *, create: bool = True) -> Path:
    """Return a user_data-backed directory for a given plugin.

    Args:
        plugin_id: Plugin identifier (used as directory name)
        create: Whether to create the directory if missing
    """

    root = plugin_data_root(create=create)
    plugin_root = root / plugin_id
    if create:
        plugin_root.mkdir(parents=True, exist_ok=True)
    return plugin_root
