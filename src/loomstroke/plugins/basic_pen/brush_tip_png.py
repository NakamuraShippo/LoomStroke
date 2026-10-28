"""Private owned PNG commits shared by selection and builtin brush tips."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from loomstroke.io.atomic_export_writer import _file_identity, write_atomically

_FileState = tuple[int, int, int, int]


def _file_state(path: Path) -> _FileState:
    value = path.lstat()
    device, inode = _file_identity(value)
    return device, inode, value.st_size, value.st_mtime_ns


@dataclass(frozen=True)
class _FileContents:
    state: _FileState
    raw: bytes


def _read_owned(path: Path) -> _FileContents:
    before = _file_state(path)
    raw = path.read_bytes()
    if _file_state(path) != before or len(raw) != before[2]:
        raise OSError("Selection tip file changed while it was read")
    return _FileContents(before, raw)


def _require_absent(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise FileExistsError("Selection tip destination already exists")


def _validate_png(raw: bytes, size: tuple[int, int]) -> None:
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Selection tip encoder did not produce a PNG")
    with Image.open(BytesIO(raw)) as decoded:
        if decoded.format != "PNG" or decoded.size != size:
            raise ValueError("Selection tip PNG does not match the captured image")
        decoded.verify()
    with Image.open(BytesIO(raw)) as decoded:
        decoded.load()


def _commit_png(destination: Path, image: Any) -> _FileContents:
    size = image.width(), image.height()
    if image.isNull() or not (0 < size[0] <= 512 and 0 < size[1] <= 512):
        raise ValueError("Selection tip image is empty or was not prepared")
    prepared: _FileContents | None = None
    stage: Path | None = None

    def encode(temporary: Path) -> None:
        nonlocal prepared, stage
        # The shared writer supplies a .tmp pathname; retain the source image's
        # format and explicitly request its existing PNG encoding.
        if not image.save(str(temporary), "PNG"):
            raise OSError("Selection tip PNG could not be saved")
        candidate = _read_owned(temporary)
        _validate_png(candidate.raw, size)
        if _read_owned(temporary) != candidate:
            raise OSError("Selection tip PNG changed after validation")
        _require_absent(destination)
        prepared, stage = candidate, temporary

    try:
        write_atomically(destination, encode)
    except Exception:
        # An OS/wrapper can report failure after the rename. Only the verified
        # owned candidate can qualify; an unrelated readable PNG never does.
        if prepared is not None and stage is not None:
            try:
                stage.lstat()
            except FileNotFoundError:
                try:
                    if _read_owned(destination) == prepared:
                        return prepared
                except OSError:
                    pass
        raise
    if prepared is None or _read_owned(destination) != prepared:
        raise OSError("Selection tip PNG commit could not be confirmed")
    return prepared
