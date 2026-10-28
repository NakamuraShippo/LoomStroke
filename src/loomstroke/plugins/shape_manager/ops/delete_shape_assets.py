"""Commit a library deletion before reclaiming provably owned standard files.

Uncertain ownership leaves orphan files, never an inferred index rollback.
Metadata checks do not exclude another process between a check and unlink.
"""

from __future__ import annotations

import json
import logging
import stat
from dataclasses import dataclass
from inspect import getattr_static
from pathlib import Path

from ..model import shape_index
from ..model.shape_index import ShapeIndex
from ..model.shape_record import ShapeRecord

logger = logging.getLogger(__name__)
_NAMES = ("shape.svg", "template.json", "thumb.png")
_FIELDS = ("svg_path", "template_path", "thumb_path")
_REPARSE = 0x400
_State = tuple[int, int, int, int]


def _one_component(value: str) -> bool:
    path = Path(value)
    return bool(value) and not (
        path.drive
        or path.anchor
        or len(path.parts) != 1
        or value in {".", ".."}
        or ":" in value
        or "/" in value
        or "\\" in value
    )


def _state(path: Path, *, directory: bool = False) -> _State:
    value = path.lstat()
    wanted = stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)
    if (
        not wanted
        or not value.st_ino
        or getattr(value, "st_file_attributes", 0) & _REPARSE
        or (not directory and value.st_nlink != 1)
    ):
        raise OSError("Shape asset ownership is unknown")
    return (
        value.st_dev,
        value.st_ino,
        0 if directory else value.st_size,
        0 if directory else value.st_mtime_ns,
    )


def _standard_index(index: ShapeIndex) -> bool:
    originals = getattr(shape_index, "_DELETE_SHAPE_INDEX_DEFAULTS", ())
    return (
        type(index) is ShapeIndex
        and bool(originals)
        and all(getattr_static(index, name, None) is method for name, method in originals)
    )


def _known_records(path: Path) -> tuple[ShapeRecord, ...]:
    before = _state(path)
    raw = path.read_bytes()
    if _state(path) != before or len(raw) != before[2]:
        raise OSError("Shape index changed during ownership checking")
    payload = json.loads(raw)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"version", "records"}
        or type(payload["version"]) is not int
        or payload["version"] != 1
        or not isinstance(payload["records"], list)
    ):
        raise ValueError("Shape index ownership cannot be determined")
    records = []
    seen: set[str] = set()
    for row in payload["records"]:
        if (
            not isinstance(row, dict)
            or set(row) - ShapeRecord.__dataclass_fields__.keys()
            or not isinstance(row.get("shape_id"), str)
            or any(not isinstance(row.get(field), str) for field in _FIELDS)
        ):
            raise ValueError("Shape row ownership cannot be determined")
        record = ShapeRecord.from_dict(row)
        if not _one_component(record.shape_id) or record.shape_id in seen:
            raise ValueError("Shape row ownership cannot be determined")
        seen.add(record.shape_id)
        records.append(record)
    return tuple(records)


def _reference(root: Path, relative: str) -> Path:
    path = Path(relative)
    if (
        path.drive
        or path.anchor
        or not path.parts
        or any(part in {".", ".."} or ":" in part for part in path.parts)
    ):
        raise ValueError("Shape reference ownership is unknown")
    current = root
    for position, part in enumerate(path.parts):
        current /= part
        try:
            _state(current, directory=position < len(path.parts) - 1)
        except FileNotFoundError:
            # Missing ordinary descendants cannot currently alias another file.
            return root / path
    return current


def _unreferenced(root: Path, directory: Path, records: tuple[ShapeRecord, ...]) -> bool:
    return all(
        not _reference(root, getattr(record, field)).is_relative_to(directory)
        for record in records
        for field in _FIELDS
    )


@dataclass(frozen=True)
class _Cleanup:
    index: ShapeIndex
    path: Path
    root: Path
    directory: Path
    files: tuple[Path, ...]
    directories: tuple[tuple[Path, _State], ...]
    states: tuple[_State, ...]
    remaining: tuple[ShapeRecord, ...]

    def _current(self) -> bool:
        if not _standard_index(self.index):
            return False
        path = self.index.path
        return (
            path == self.path
            and tuple(self.index.all()) == self.remaining
            and _known_records(path) == self.remaining
            and self.index.path == path
            and _unreferenced(self.root, self.directory, self.remaining)
        )

    def collect(self) -> None:
        try:
            for path, identity in self.directories:
                if _state(path, directory=True) != identity:
                    return
            if not self._current() or set(self.directory.iterdir()) != set(self.files):
                return
            if any(
                _state(path) != expected
                for path, expected in zip(self.files, self.states, strict=True)
            ):
                return
            for position, (path, expected) in enumerate(zip(self.files, self.states, strict=True)):
                if any(
                    _state(parent, directory=True) != state for parent, state in self.directories
                ):
                    return
                if (
                    not self._current()
                    or set(self.directory.iterdir()) != set(self.files[position:])
                    or _state(path) != expected
                ):
                    return
                path.unlink()
            if (
                not any(self.directory.iterdir())
                and all(
                    _state(parent, directory=True) == state for parent, state in self.directories
                )
                and self._current()
            ):
                self.directory.rmdir()
        except Exception:
            logger.warning("Some shape assets were retained after the index deletion")


def _prepare(index: ShapeIndex, record: ShapeRecord, root: Path) -> _Cleanup | None:
    try:
        root = root.absolute()
        path = index.path
        if path.absolute() != root / "index.json" or not _one_component(record.shape_id):
            return None
        directory = root / "shapes" / record.shape_id
        parents = (directory, *directory.parents)
        directories = tuple((path, _state(path, directory=True)) for path in parents)
        if root.resolve(strict=True) != root or directory.resolve(strict=True) != directory:
            return None
        files = tuple(directory / name for name in _NAMES)
        if any(
            Path(getattr(record, field)) != Path("shapes") / record.shape_id / name
            for field, name in zip(_FIELDS, _NAMES, strict=True)
        ) or set(directory.iterdir()) != set(files):
            return None
        states = tuple(_state(path) for path in files)
        records = _known_records(path)
        remaining = tuple(item for item in records if item.shape_id != record.shape_id)
        if records != tuple(index.all()) or not _unreferenced(root, directory, remaining):
            return None
        return _Cleanup(index, path, root, directory, files, directories, states, remaining)
    except Exception:
        logger.debug("Shape assets retained because ownership could not be established")
        return None


def delete_shape_record(index: ShapeIndex, shape_id: str, record: ShapeRecord, root: Path) -> bool:
    """Preserve Index.remove's positional call and exceptions before asset I/O."""
    standard = _standard_index(index)
    if standard and index.get(shape_id) is not record:
        return False
    cleanup = _prepare(index, record, root) if standard else None
    index.remove(shape_id)
    if cleanup is not None:
        cleanup.collect()
    return True
