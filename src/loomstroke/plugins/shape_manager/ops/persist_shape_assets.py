"""Prepare a shape's existing payloads and create only a new owned directory."""

from __future__ import annotations

import json
import logging
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from loomstroke.io.atomic_export_writer import write_atomically

from ..model.shape_index_io import _check_file, _confirmed_destination, _file_state
from ..model.shape_record import ShapeRecord, ShapeTemplate

logger = logging.getLogger(__name__)


def _directory_identity(path: Path, *, follow: bool = False) -> tuple[int, int]:
    value = path.stat() if follow else path.lstat()
    if (
        not value.st_ino
        or not stat.S_ISDIR(value.st_mode)
        or (not follow and getattr(value, "st_file_attributes", 0) & 0x400)
    ):
        raise OSError("Shape output directory ownership is unknown")
    return value.st_dev, value.st_ino


def _require_absent(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise FileExistsError("A shape artifact already exists")


@dataclass(frozen=True)
class _Artifact:
    path: Path
    identity: tuple[int, int, int, int]
    raw: bytes

    def confirm(self) -> None:
        _check_file(self.path, self.identity, self.raw)


class _NewShapeDirectory:
    def __init__(self, root: Path, shape_id: str) -> None:
        self.parent = root / "shapes"
        self.directory = self.parent / shape_id
        if self.directory.parent != self.parent or self.directory.name != shape_id:
            raise ValueError("Shape output must use one new directory")
        self.parent.mkdir(parents=True, exist_ok=True)
        # A configured parent may be an intentional filesystem alias. Track its
        # actual directory, while the exclusively created child must not be one.
        self._parent_id = _directory_identity(self.parent, follow=True)
        self.directory.mkdir()
        self._identity = _directory_identity(self.directory)
        self._artifacts: list[_Artifact] = []

    def confirm(self) -> None:
        if (
            _directory_identity(self.parent, follow=True) != self._parent_id
            or _directory_identity(self.directory) != self._identity
        ):
            raise OSError("Shape output directory changed during creation")

    def write(self, name: str, encode: Callable[[Path], bytes]) -> None:
        destination = self.directory / name
        self.confirm()
        _require_absent(destination)
        candidate: _Artifact | None = None
        stage: Path | None = None

        def prepare(temporary: Path) -> None:
            nonlocal candidate, stage
            raw = encode(temporary)
            identity = _file_state(temporary)
            _check_file(temporary, identity, raw)
            self.confirm()
            _require_absent(destination)
            candidate, stage = _Artifact(destination, identity, raw), temporary

        try:
            write_atomically(destination, prepare)
        except Exception:
            complete = False
            try:
                if candidate is not None and stage is not None:
                    self.confirm()
                    complete = _confirmed_destination(
                        destination, stage, candidate.identity, candidate.raw
                    )
            except Exception:
                logger.debug("Shape artifact replacement could not be confirmed")
            if not complete:
                raise
        if candidate is None:
            raise OSError("Shape artifact preparation did not complete")
        self.confirm()
        candidate.confirm()
        self._artifacts.append(candidate)

    def finalize(self) -> None:
        self.confirm()
        for artifact in self._artifacts:
            artifact.confirm()
        self.confirm()

    def reclaim(self) -> None:
        """Reclaim only this unchanged directory's confirmed outputs.

        Unknown children, renamed directories and foreign replacements remain.
        Check/unlink pairs do not exclude other processes or promise recovery
        from a crash. The original preparation/write exception stays primary.
        """
        try:
            self.confirm()
            paths = {artifact.path for artifact in self._artifacts}
            if set(self.directory.iterdir()) != paths:
                return
            for artifact in self._artifacts:
                artifact.confirm()
            for position, artifact in enumerate(self._artifacts):
                self.confirm()
                remaining = {entry.path for entry in self._artifacts[position:]}
                if set(self.directory.iterdir()) != remaining:
                    return
                artifact.confirm()
                artifact.path.unlink()
            self.confirm()
            if not any(self.directory.iterdir()):
                self.directory.rmdir()
        except Exception:
            logger.warning("Some new shape outputs were retained because ownership is uncertain")


def _text_encoder(text: str) -> Callable[[Path], bytes]:
    wire = text.replace("\n", os.linesep).encode("utf-8")

    def encode(path: Path) -> bytes:
        with path.open("w", encoding="utf-8") as stream:
            if stream.write(text) != len(text):
                raise OSError("Shape artifact write was incomplete")
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != wire:
            raise OSError("Shape artifact differs from its prepared content")
        return wire

    return encode


def _thumbnail_encoder(thumbnail: Any) -> Callable[[Path], bytes]:
    size = thumbnail.width(), thumbnail.height()

    def encode(path: Path) -> bytes:
        if not thumbnail.save(str(path), "PNG"):
            raise OSError("Shape thumbnail could not be saved")
        with path.open("r+b") as stream:
            os.fsync(stream.fileno())
            raw = stream.read()
        with Image.open(BytesIO(raw)) as image:
            if image.format != "PNG" or image.size != size:
                raise ValueError("Shape thumbnail does not match its prepared image")
            image.verify()
        with Image.open(BytesIO(raw)) as image:
            image.load()
        return raw

    return encode


def persist_shape_assets(
    *,
    root: Path,
    shape_id: str,
    name: str,
    category: str,
    tags: tuple[str, ...],
    svg_text: str,
    template: ShapeTemplate,
    render_thumbnail: Callable[..., Any],
    timestamp: str,
    default_size: int,
) -> ShapeRecord:
    """Prepare fallible callbacks before creating an exclusive UUID directory.

    Missing/null thumbnails remain legal and keep the existing relative path.
    Existing wire encoding, metadata and settings values are not reinterpreted.
    Successful return describes persisted assets; index registration belongs to
    the caller, and is not a transaction with these individual file commits.
    """
    template_text = json.dumps(template.to_dict(), ensure_ascii=False, indent=2)
    thumbnail = render_thumbnail(svg_text, size=128)
    encoders = [
        ("shape.svg", _text_encoder(svg_text)),
        ("template.json", _text_encoder(template_text)),
    ]
    if thumbnail is not None and not thumbnail.isNull():
        encoders.append(("thumb.png", _thumbnail_encoder(thumbnail)))
    relative = Path("shapes") / shape_id
    record = ShapeRecord(
        shape_id=shape_id,
        name=name,
        category=category,
        tags=tags,
        created_at_iso=timestamp,
        updated_at_iso=timestamp,
        svg_path=str(relative / "shape.svg"),
        template_path=str(relative / "template.json"),
        thumb_path=str(relative / "thumb.png"),
        default_insert_policy="default_size",
        default_size_px=default_size,
    )
    owner = _NewShapeDirectory(root, shape_id)
    try:
        for filename, encode in encoders:
            owner.write(filename, encode)
        owner.finalize()
        return record
    except Exception:
        owner.reclaim()
        raise
