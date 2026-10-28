"""Explicit, bounded image diagnostics; ordinary editing never creates files."""

from __future__ import annotations

import logging
import os
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from .user_dirs import default_user_root

logger = logging.getLogger(__name__)
_MAX_FILES = 32
_MAX_BYTES = 64 * 1024 * 1024
_WRITE_LOCK = threading.Lock()
_REPARSE_POINT = 0x400
_PayloadBuilder = Callable[[], tuple[bytes | None, bytes | None]]
_Identity = tuple[int, int]
_Directories = list[tuple[Path, _Identity]]


def _identity(value: os.stat_result, *, directory: bool) -> _Identity:
    if not value.st_ino:
        raise OSError("Unknown diagnostic path identity")
    if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & _REPARSE_POINT:
        raise OSError("Linked diagnostic path")
    if directory:
        if not stat.S_ISDIR(value.st_mode):
            raise OSError("Diagnostic parent is not a directory")
    elif not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise OSError("Diagnostic file is not an unlinked regular file")
    return value.st_dev, value.st_ino


def _check_directories(directories: _Directories) -> None:
    for path, expected in directories:
        if _identity(path.lstat(), directory=True) != expected:
            raise OSError("Diagnostic directory changed")
        if os.path.normcase(str(path.resolve())) != os.path.normcase(str(path)):
            raise OSError("Diagnostic directory resolves elsewhere")


def _prepare_directory(root: Path) -> tuple[Path, _Directories]:
    root = root.absolute()
    output = root / "diagnostics" / "image_dumps"
    directories: _Directories = []
    for path in reversed((output, *output.parents)):
        _check_directories(directories)
        try:
            value = path.lstat()
        except FileNotFoundError:
            path.mkdir()
            value = path.lstat()
        directories.append((path, _identity(value, directory=True)))
    _check_directories(directories)
    return output, directories


def _usage(output: Path, directories: _Directories) -> tuple[int, int]:
    _check_directories(directories)
    count = size = 0
    for path in output.iterdir():
        value = path.lstat()
        _identity(value, directory=False)
        count += 1
        size += value.st_size
    _check_directories(directories)
    return count, size


def _same_file(path: Path, expected: _Identity) -> bool:
    return _identity(path.lstat(), directory=False) == expected


def _remove_owned_files(created: list[tuple[Path, _Identity]], directories: _Directories) -> None:
    for path, expected in reversed(created):
        try:
            _check_directories(directories)
            if _same_file(path, expected):
                path.unlink()
        except Exception:
            # An unknown identity or changed directory must never be cleaned up.
            logger.debug("Incomplete image diagnostic could not be removed")


def _write_payload(
    path: Path, payload: bytes, directories: _Directories, created: list[tuple[Path, _Identity]]
) -> None:
    _check_directories(directories)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        expected = _identity(os.fstat(descriptor), directory=False)
        created.append((path, expected))
        _check_directories(directories)
        if not _same_file(path, expected):
            raise OSError("Diagnostic file identity changed")
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
        _check_directories(directories)
        if not _same_file(path, expected):
            raise OSError("Diagnostic file identity changed")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def write_image_diagnostic(component: str, build_payloads: _PayloadBuilder) -> bool:
    """Write optional PNG/JSON only for the dedicated environment opt-in.

    Quotas include unknown regular files and are rechecked after lazy encoding.
    No existing artifact is removed. Identity checks reject observed link/race
    changes; they do not provide native exclusion against hostile OS replacement.
    """
    if os.environ.get("LOOMSTROKE_IMAGE_DIAGNOSTICS") != "1":
        return False
    if component not in {"rig", "comfy_slots"} or not _WRITE_LOCK.acquire(blocking=False):
        return False
    created: list[tuple[Path, _Identity]] = []
    directories: _Directories = []
    try:
        output, directories = _prepare_directory(default_user_root(create=False))
        count, size = _usage(output, directories)
        expected_files = 2 if component == "rig" else 1
        if count + expected_files > _MAX_FILES or size >= _MAX_BYTES:
            return False
        png, metadata = build_payloads()
        payloads = [
            (suffix, payload)
            for suffix, payload in ((".png", png), (".json", metadata))
            if payload is not None
        ]
        if not payloads or any(not isinstance(payload, bytes) for _suffix, payload in payloads):
            raise ValueError("Invalid diagnostic payload")
        count, size = _usage(output, directories)
        if (
            count + len(payloads) > _MAX_FILES
            or size + sum(len(payload) for _suffix, payload in payloads) > _MAX_BYTES
        ):
            return False
        stem = f"{component}_{uuid4().hex}"
        for suffix, payload in payloads:
            count, size = _usage(output, directories)
            if count + 1 > _MAX_FILES or size + len(payload) > _MAX_BYTES:
                raise OSError("Diagnostic quota changed")
            _write_payload(output / f"{stem}{suffix}", payload, directories, created)
    except Exception:
        _remove_owned_files(created, directories)
        logger.debug("Image diagnostic skipped")
        return False
    finally:
        _WRITE_LOCK.release()
    return True
