"""Publish a complete set of temporary ABR tips as one reusable cache."""

from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from .abr_importer import ABRImporter

_COMPLETE = ".complete"


def _tip_paths(directory: Path, count: int) -> list[Path]:
    return [directory / f"tip_{index:03d}.png" for index in range(count)]


def _read_cache(directory: Path) -> list[Path] | None:
    try:
        count = int((directory / _COMPLETE).read_text(encoding="ascii"))
        files = set(directory.glob("*.png"))
    except (OSError, ValueError):
        return None
    if count != len(files):
        return None
    paths = _tip_paths(directory, count)
    return paths if set(paths) == files and all(path.is_file() for path in paths) else None


def _publish_cache(staged: Path, destination: Path, backup: Path) -> None:
    # Windows cannot replace a non-empty directory in one rename. Keep the
    # previous cache outside the staging cleanup until publication succeeds.
    had_cache = destination.exists()
    if had_cache:
        destination.rename(backup)
    try:
        staged.rename(destination)
    except OSError:
        if had_cache:
            backup.rename(destination)
        raise
    if had_cache:
        shutil.rmtree(backup, ignore_errors=True)


def load_extracted_tips(
    importer: ABRImporter, source: Path, directory: Path, *, force_extract: bool
) -> list[Path]:
    if not force_extract:
        cached = _read_cache(directory)
        if cached is not None:
            return cached

    directory.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".abr-extract-", dir=directory.parent) as tmp:
        work = Path(tmp)
        staged = work / "tips"
        staged.mkdir()
        count = len(importer.extract_tips(str(source), str(staged)))
        (staged / _COMPLETE).write_text(str(count), encoding="ascii")
        if _read_cache(staged) is None:
            raise OSError("ABR extraction did not write the complete set of tips")
        _publish_cache(staged, directory, work.with_name(f"{work.name}.previous"))
    return _tip_paths(directory, count)
