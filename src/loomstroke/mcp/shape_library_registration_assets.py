"""Short-lived evidence for reclaiming an unused shape registration's outputs."""

from __future__ import annotations

import logging
import stat
from dataclasses import dataclass
from pathlib import Path

from loomstroke.plugins.shape_manager.model.shape_index import ShapeIndex
from loomstroke.plugins.shape_manager.model.shape_index_io import (
    _check_file,
    _file_state,
    _IndexSource,
    _read_source,
)
from loomstroke.plugins.shape_manager.model.shape_record import ShapeRecord

logger = logging.getLogger(__name__)
_FIELDS = (
    ("svg_path", "shape.svg"),
    ("template_path", "template.json"),
    ("thumb_path", "thumb.png"),
)


def _directory_identity(path: Path) -> tuple[int, int]:
    value = path.lstat()
    if (
        not value.st_ino
        or not stat.S_ISDIR(value.st_mode)
        or getattr(value, "st_file_attributes", 0) & 0x400
    ):
        raise OSError("Shape registration directory ownership is unknown")
    return value.st_dev, value.st_ino


@dataclass(frozen=True)
class _Artifact:
    path: Path
    identity: tuple[int, int, int, int]
    raw: bytes

    def confirm(self) -> None:
        _check_file(self.path, self.identity, self.raw)


class _RegistrationAssets:
    def __init__(self, index: ShapeIndex, root: Path) -> None:
        if index.path.absolute() != root.absolute() / "index.json":
            raise OSError("The shape index does not match the asset root")
        if index._load_failure is not None:
            raise OSError("The shape index must be reloaded before registration")
        self._index = index
        self._records_ref, self._lookup_ref = index._records, index._by_id
        self._records = tuple(self._records_ref)
        self._lookup = tuple(self._lookup_ref.items())
        self._source: _IndexSource = _read_source(index.path, self._records)
        if self._source.identity is None and index._source_seen:
            raise OSError("An existing shape index is no longer available")
        self._input_root = root
        self._root = root.resolve()
        self._shapes = self._root / "shapes"
        self._shapes_before: tuple[int, int] | None
        self._names: frozenset[str]
        try:
            self._shapes_before = _directory_identity(self._shapes)
        except FileNotFoundError:
            self._shapes_before = None
            self._names = frozenset()
        else:
            self._names = frozenset(path.name for path in self._shapes.iterdir())
            if _directory_identity(self._shapes) != self._shapes_before:
                raise OSError("Shape registration directory changed during preparation")
        self._directory: Path | None = None
        self._directory_id: tuple[int, int] | None = None
        self._shapes_id: tuple[int, int] | None = None
        self._artifacts: tuple[_Artifact, ...] | None = None
        self._paths: set[Path] = set()
        self._source.confirm()
        if not self._cache_current() or index._load_failure is not None:
            raise OSError("Shape registration source changed during preparation")

    def _cache_current(self) -> bool:
        index = self._index
        return (
            index.path == self._source.path
            and index._records is self._records_ref
            and index._by_id is self._lookup_ref
            and len(index._records) == len(self._records)
            and all(
                left is right for left, right in zip(index._records, self._records, strict=True)
            )
            and len(index._by_id) == len(self._lookup)
            and all(index._by_id.get(key) is value for key, value in self._lookup)
        )

    def target_current(self, index: ShapeIndex, root: Path) -> bool:
        return index is self._index and index.path == self._source.path and root == self._input_root

    def observe(self, record: ShapeRecord) -> None:
        """Observation never authorizes reclaiming unknown or preexisting outputs."""
        try:
            directory = self._shapes / record.shape_id
            if directory.parent != self._shapes or directory.name in self._names:
                return
            shapes_id = _directory_identity(self._shapes)
            if self._shapes_before is not None and shapes_id != self._shapes_before:
                return
            directory_id = _directory_identity(directory)
            artifacts: list[_Artifact] = []
            paths: set[Path] = set()
            for field, filename in _FIELDS:
                path = self._root / getattr(record, field)
                if path != directory / filename:
                    return
                paths.add(path)
                try:
                    identity = _file_state(path)
                except FileNotFoundError:
                    continue  # Thumbnail generation may legitimately create no file.
                raw = path.read_bytes()
                _check_file(path, identity, raw)
                artifacts.append(_Artifact(path, identity, raw))
            if _directory_identity(directory) != directory_id:
                return
            self._directory, self._directory_id = directory, directory_id
            self._shapes_id = shapes_id
            self._artifacts, self._paths = tuple(artifacts), paths
        except Exception:
            logger.warning("Shape registration outputs retained because ownership is unknown")

    def _unreferenced(self, record: ShapeRecord) -> bool:
        if self._index.get(record.shape_id) is not None:
            return False
        for row in self._source.payload.get("records", []):
            if not isinstance(row, dict):
                continue
            if str(row.get("shape_id", "")) == record.shape_id:
                return False
            for field, _filename in _FIELDS:
                if field in row and (self._root / str(row[field])).resolve() in self._paths:
                    return False
        return True

    def reclaim_unused(self, record: ShapeRecord) -> None:
        """Preserve uncertain/foreign state and never replace the primary error.

        Final check/unlink pairs do not exclude other processes or guarantee
        recovery after a crash. Failed persist calls before a record is returned
        do not supply this receipt with outputs and are outside its scope.
        """
        if self._artifacts is None or self._directory is None:
            return
        try:
            self._source.confirm()
            if not self._cache_current() or not self._unreferenced(record):
                return
            if (
                _directory_identity(self._shapes) != self._shapes_id
                or _directory_identity(self._directory) != self._directory_id
            ):
                return
            for artifact in self._artifacts:
                artifact.confirm()
            for artifact in self._artifacts:
                self._source.confirm()
                if not self._cache_current():
                    return
                artifact.confirm()
                if _directory_identity(self._directory) != self._directory_id:
                    return
                artifact.path.unlink()
            if _directory_identity(self._directory) == self._directory_id and not any(
                self._directory.iterdir()
            ):
                self._directory.rmdir()
        except Exception:
            logger.warning(
                "Shape registration outputs retained because unused ownership is uncertain"
            )
