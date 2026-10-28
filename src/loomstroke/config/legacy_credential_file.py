"""Guarded, atomic writes of the existing encrypted legacy credential file."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


def _identity(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


@dataclass(frozen=True, slots=True)
class LegacyCredentialFile:
    path: Path
    encrypted: bytes
    identity: tuple[int, int, int, int]

    def assert_current(self) -> None:
        if _identity(self.path) != self.identity or self.path.read_bytes() != self.encrypted:
            raise OSError("Legacy credential material changed")


def read_legacy_file(path: Path) -> LegacyCredentialFile | None:
    if not path.exists():
        return None
    before = _identity(path)
    encrypted = path.read_bytes()
    if _identity(path) != before:
        raise OSError("Legacy credential material changed while reading")
    return LegacyCredentialFile(path, encrypted, before)


def assert_legacy_source(path: Path, expected: LegacyCredentialFile | None) -> None:
    if expected is not None:
        expected.assert_current()
    elif path.exists():
        raise OSError("Legacy credential material appeared while processing")


def _unlink_own_temp(path: Path, identity: tuple[int, int]) -> None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return
    if (stat.st_dev, stat.st_ino) == identity:
        path.unlink()


def replace_legacy_file(
    destination: Path,
    encrypted: bytes,
    *,
    expected: LegacyCredentialFile | None,
    key_path: Path,
    expected_key: LegacyCredentialFile | None,
    restrict_file_acl: Callable[[Path], None],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(filename)
    stat = os.fstat(descriptor)
    identity = stat.st_dev, stat.st_ino
    try:
        restrict_file_acl(temporary)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        stat = temporary.stat()
        if (stat.st_dev, stat.st_ino) != identity:
            raise OSError("Legacy credential temporary file changed")
        assert_legacy_source(destination, expected)
        assert_legacy_source(key_path, expected_key)
        os.replace(temporary, destination)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _unlink_own_temp(temporary, identity)
