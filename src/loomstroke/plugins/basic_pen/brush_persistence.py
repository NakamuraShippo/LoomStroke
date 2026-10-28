"""Validate brush JSON candidates before committing their existing-format files."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loomstroke.io.atomic_export_writer import _file_identity, write_atomically

_FileState = tuple[int, int, int, int]


def _file_state(path: Path) -> _FileState:
    value = path.lstat()
    return _metadata_state(value)


def _metadata_state(value: os.stat_result) -> _FileState:
    device, inode = _file_identity(value)
    return device, inode, value.st_size, value.st_mtime_ns


def _check_file(path: Path, identity: _FileState, expected: bytes) -> None:
    if (
        _file_state(path) != identity
        or path.read_bytes() != expected
        or _file_state(path) != identity
    ):
        raise OSError("Brush recovery file changed after it was read")


def _require_absent(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise FileExistsError("Brush recovery backup already exists")


@dataclass(frozen=True)
class _BrushFileRead:
    path: Path
    metadata: os.stat_result
    raw: bytes

    def parse(self) -> dict[str, Any]:
        # Decode explicitly to retain read_text's existing UTF-8/BOM behavior.
        value = json.loads(self.raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise TypeError("Brush JSON must contain an object at the root")
        return value

    def confirm_original(self) -> None:
        _check_file(self.path, _metadata_state(self.metadata), self.raw)


def read_brush_json(path: Path) -> _BrushFileRead:
    """Read without widening the qualification of successful normal JSON loads.

    Automatic repair alone requires a regular, unlinked original and matching
    pre-read metadata. Read/metadata failures propagate without recovery writes.
    """
    metadata = path.lstat()
    return _BrushFileRead(path, metadata, path.read_bytes())


def _confirmed_destination(path: Path, stage: Path, identity: _FileState, expected: bytes) -> bool:
    try:
        try:
            stage.lstat()
        except FileNotFoundError:
            pass
        else:
            return False
        return (
            _file_state(path) == identity
            and path.read_bytes() == expected
            and _file_state(path) == identity
        )
    except OSError:
        return False


def commit_brush_json(
    destination: Path,
    value: dict[str, Any],
    *,
    _commit_guard: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Return a detached, wire-normalized cache only after the file commits.

    Keep the existing indent/sort/ASCII, platform newline, tuple and nonfinite
    JSON behavior. The shared writer owns temporary cleanup; it does not add
    exclusion against another process or a power-failure durability guarantee.
    """
    text = json.dumps(deepcopy(value), indent=2, sort_keys=True)
    wire = text.replace("\n", os.linesep).encode("utf-8")
    missing = object()
    prepared: Any = missing
    identity: _FileState | None = None
    stage: Path | None = None

    def encode(temporary: Path) -> None:
        nonlocal prepared, identity, stage
        with temporary.open("w", encoding="utf-8") as stream:
            if stream.write(text) != len(text):
                raise OSError("Brush JSON candidate write was incomplete")
            stream.flush()
            os.fsync(stream.fileno())
        actual = temporary.read_bytes()
        if actual != wire:
            raise OSError("Brush JSON candidate differs from its prepared content")
        prepared = json.loads(actual)
        identity = _file_state(temporary)
        stage = temporary
        if _commit_guard is not None:
            _commit_guard()

    try:
        write_atomically(destination, encode)
    except Exception:
        # A wrapper/OS error can be reported after the rename already completed.
        # Do not adopt another file merely because its JSON happens to look valid.
        if (
            prepared is not missing
            and identity is not None
            and stage is not None
            and _confirmed_destination(destination, stage, identity, wire)
        ):
            return prepared
        raise
    if prepared is missing:
        raise RuntimeError("Brush JSON candidate was not prepared")
    return prepared


def recover_brush_json(
    source: _BrushFileRead, backup: Path, default: dict[str, Any]
) -> dict[str, Any]:
    """Preserve a verified raw backup before replacing a corrupt original.

    Failed preparation keeps the original and any completed backup. Observed
    foreign files are never overwritten or rolled back. Final check/rename
    pairs do not provide exclusion against another process or a crash.
    """
    source.confirm_original()
    _require_absent(backup)
    identity: _FileState | None = None
    stage: Path | None = None

    def encode(temporary: Path) -> None:
        nonlocal identity, stage
        with temporary.open("wb") as stream:
            if stream.write(source.raw) != len(source.raw):
                raise OSError("Brush recovery backup write was incomplete")
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.read_bytes() != source.raw:
            raise OSError("Brush recovery backup differs from the original bytes")
        identity = _file_state(temporary)
        stage = temporary
        source.confirm_original()
        _require_absent(backup)

    try:
        write_atomically(backup, encode, _allow_empty=not source.raw)
    except Exception:
        if not (
            identity is not None
            and stage is not None
            and _confirmed_destination(backup, stage, identity, source.raw)
        ):
            raise
    if identity is None:
        raise RuntimeError("Brush recovery backup was not prepared")

    def confirm_recovery() -> None:
        source.confirm_original()
        _check_file(backup, identity, source.raw)

    confirm_recovery()
    return commit_brush_json(source.path, default, _commit_guard=confirm_recovery)
