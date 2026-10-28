"""Atomically replace an export with an owned, normally nonempty temporary.

The encoder must close/flush its output and perform format/content validation.
This helper does not add fsync or promise durability across a power failure.
Observed ownership changes are rejected; unknown or unrelated files are kept.
Identity checks do not provide native exclusion against hostile OS replacement.
"""

from __future__ import annotations

import logging
import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

__all__ = ["write_atomically"]

logger = logging.getLogger(__name__)
_Identity = tuple[int, int]
_REPARSE_POINT = 0x400


def _file_identity(value: os.stat_result) -> _Identity:
    if (
        not value.st_ino
        or not stat.S_ISREG(value.st_mode)
        or getattr(value, "st_file_attributes", 0) & _REPARSE_POINT
        or value.st_nlink != 1
    ):
        raise OSError("Atomic export temporary has unknown or linked ownership")
    return value.st_dev, value.st_ino


def _owned_file(path: Path, expected: _Identity) -> os.stat_result:
    value = path.lstat()
    if _file_identity(value) != expected:
        raise OSError("Atomic export temporary ownership changed")
    return value


def _cleanup_owned(path: Path, expected: _Identity) -> None:
    try:
        _owned_file(path, expected)
        path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        # Cleanup must preserve the encoder error and any file we cannot own.
        logger.debug("Atomic export temporary retained after cleanup could not be confirmed")


def write_atomically(
    destination: Path, encode: Callable[[Path], None], *, _allow_empty: bool = False
) -> int:
    """Run ``encode(temp_path)`` and atomically move the result in place.

    Return the byte count and propagate encoder exceptions unchanged. An empty
    temporary is rejected by default; the private exception is for a validated
    exact empty recovery copy. Missing, linked or replaced temporaries are
    always rejected. The callback receives a closed, already-created file with
    a unique short name.
    Failed writes reclaim only their own identifiable temporary; cleanup failure
    can leave it behind but does not replace the original error.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".ls-export-", suffix=".tmp", dir=destination.parent)
    temp_path: Path | None = Path(name)
    identity: _Identity | None = None
    try:
        try:
            identity = _file_identity(os.fstat(descriptor))
        finally:
            os.close(descriptor)
        _owned_file(temp_path, identity)
        encode(temp_path)
        value = _owned_file(temp_path, identity)
        if value.st_size == 0 and not _allow_empty:
            raise OSError(f"encoder produced no output for {destination.name}")
        size = value.st_size
        os.replace(temp_path, destination)
        temp_path = None
        return size
    finally:
        if temp_path is not None and identity is not None:
            _cleanup_owned(temp_path, identity)
