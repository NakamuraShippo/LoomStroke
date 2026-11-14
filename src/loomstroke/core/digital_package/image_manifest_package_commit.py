"""Publish a checked image package without replacing unrelated directory contents."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path


class PackageDirectoryRollbackError(OSError):
    """The original files that could not be restored remain in the named backup."""

    def __init__(self, backup_name: str) -> None:
        super().__init__(backup_name)
        self.backup_name = backup_name


def _exists_as(path: Path, *, directory: bool) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise OSError("Image package destination contains a conflicting or linked entry")
    return True


def commit_image_package(staged: Path, destination: Path, members: tuple[Path, ...]) -> None:
    """Commit a new folder at once, or replace selected files with rollback."""
    if any(member.anchor or not member.name or ".." in member.parts for member in members):
        raise ValueError("Invalid image package member")
    if not _exists_as(destination, directory=True):
        os.replace(staged, destination)
        return

    parents = sorted(
        {destination / parent for member in members for parent in member.parents if parent.parts},
        key=lambda path: len(path.parts),
    )
    missing_parents = [parent for parent in parents if not _exists_as(parent, directory=True)]
    originals = {member for member in members if _exists_as(destination / member, directory=False)}
    backup = Path(tempfile.mkdtemp(prefix=".ls-package-backup-", dir=destination.parent))
    created_parents: list[Path] = []
    journal: list[tuple[Path, Path | None]] = []
    keep_backup = False
    try:
        for parent in missing_parents:
            parent.mkdir()
            created_parents.append(parent)
        for member in members:
            target = destination / member
            if member in originals:
                old = backup / member
                old.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, old)
                journal.append((target, old))
                os.replace(staged / member, target)
            else:
                os.replace(staged / member, target)
                journal.append((target, None))
    except Exception as error:
        keep_backup = not _restore(journal, created_parents)
        if keep_backup:
            raise PackageDirectoryRollbackError(backup.name) from error
        raise
    finally:
        if not keep_backup:
            shutil.rmtree(backup, ignore_errors=True)


def _restore(journal: list[tuple[Path, Path | None]], created_parents: list[Path]) -> bool:
    complete = True
    for target, original in reversed(journal):
        try:
            if original is None:
                target.unlink(missing_ok=True)
            else:
                os.replace(original, target)
        except OSError:
            complete = False
    for parent in reversed(created_parents):
        try:
            parent.rmdir()
        except OSError:
            complete = False
    return complete
