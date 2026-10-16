"""Shared atomic write helpers.

Code review #4 + #8 fix — ``settings.py``, ``state.py``, and ``lsfx_io.py``
previously had near-identical atomic-write code with **inconsistent fsync
calls** (lsfx_io was missing fsync entirely). This module extracts the
shared algorithm so a single fix applies everywhere.

Algorithm (both helpers):

1. Create a temp file in the **same directory** as ``path`` via
   ``tempfile.mkstemp`` so ``os.replace`` can be atomic even across file
   systems.
2. Write payload, ``flush()``, ``os.fsync(fileno)`` — durability guarantee.
3. ``os.replace(tmp_path, path)`` — atomic rename.
4. On any exception, remove the temp file and re-raise the original.

Crash-safety: a crash between steps 2 and 3 leaves the original file
untouched (or absent). There is never a "half-written final path" state.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def atomic_write_json(path: Path, payload: Any) -> None:
    """Serialize ``payload`` as JSON and write it atomically to ``path``.

    ``payload`` may contain non-ASCII text (UTF-8 preserved via
    ``ensure_ascii=False``) and is indented for readability.
    """

    def _write(f: Any) -> None:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    atomic_write_text(path, _write)


def atomic_write_text(path: Path, writer: Callable[[Any], None]) -> None:
    """Atomic text-mode write. ``writer(fileobj)`` performs the actual dump."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path_str = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=str(path.parent))
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            writer(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        _cleanup_tmp(tmp_path)
        raise


def atomic_write_binary(path: Path, writer: Callable[[Path], None]) -> None:
    """Atomic binary-mode write where the writer takes the temp path directly.

    Used for producers that need to open the file themselves (e.g.
    ``zipfile.ZipFile(tmp_path, "w", ...)``). The writer is responsible for
    opening and closing the file; this helper only handles the temp-file
    lifecycle, fsync, and rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        prefix=path.stem + ".tmp.",
        suffix=path.suffix,
        dir=str(path.parent),
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)
    try:
        writer(tmp_path)
        # Best-effort fsync to flush kernel buffers to disk before rename.
        # Windows does not support fsync on O_RDONLY handles, so the fsync
        # is POSIX-only. On Windows we rely on the writer having closed
        # the file (which flushes) plus the atomic rename.
        if os.name == "posix":
            fd = os.open(str(tmp_path), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        os.replace(tmp_path, path)
    except Exception:
        _cleanup_tmp(tmp_path)
        raise


def _cleanup_tmp(tmp_path: Path) -> None:
    """Best-effort cleanup of a temp file after a failed atomic write."""
    if tmp_path.exists():
        try:
            tmp_path.unlink()
        except OSError:
            logger.debug("Could not clean up temp file %s", tmp_path, exc_info=True)
