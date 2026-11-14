"""Keep the selected ABR's bytes separate from previously imported names."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from loomstroke.io.atomic_export_writer import write_atomically


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def store_abr_source(source: Path, import_directory: Path) -> Path:
    destination = import_directory / source.name
    source_digest = None
    if destination.exists() or destination.is_symlink():
        if source.samefile(destination):
            return destination
        source_digest = _digest(source)
        if _digest(destination) == source_digest:
            return destination
        destination = import_directory / f"{source.stem}-{source_digest[:12]}{source.suffix}"
        if destination.exists() or destination.is_symlink():
            if _digest(destination) == source_digest:
                return destination
            raise FileExistsError(destination)

    def copy_source(temporary: Path) -> None:
        shutil.copyfile(source, temporary)
        if source_digest is not None and _digest(temporary) != source_digest:
            raise OSError("ABR source changed during import")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(destination)

    write_atomically(destination, copy_source)
    return destination
