"""Bind page reads to the identity of the archive this save actually wrote."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..io.layer_io import LayerIO, PageSnapshot

FileIdentity = tuple[int, int, int, int]


def file_identity(path: Path) -> FileIdentity:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


@dataclass(frozen=True, slots=True)
class SavedArchiveReceipt:
    """Identity captured from the encoded artifact before its final rename."""

    path: Path
    identity: FileIdentity

    def assert_current(self) -> None:
        if file_identity(self.path) != self.identity:
            raise OSError(f"Saved archive changed before it could be confirmed: {self.path.name}")


@dataclass(frozen=True, slots=True)
class SavedPageSource:
    """A main-thread loader; neither a later stat nor an old lease retargets it."""

    receipt: SavedArchiveReceipt
    layer_io: LayerIO
    strict: bool = True

    @property
    def path(self) -> Path:
        return self.receipt.path

    def __call__(self, page_id: str) -> PageSnapshot | None:
        self.receipt.assert_current()
        page = self.layer_io.load_single_page_snapshot(self.path, page_id, strict=self.strict)
        self.receipt.assert_current()
        return page
