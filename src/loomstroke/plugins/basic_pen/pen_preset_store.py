"""Atomic file I/O and the local library for LoomStroke pen presets."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from loomstroke.platform.user_dirs import plugin_data_dir

from .pen_preset import MAX_PRESET_BYTES, PEN_EXTENSION, PenPreset, validate_preset_id

logger = logging.getLogger(__name__)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate pen document key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError(f"Invalid pen document number: {value}")


def load_pen_preset(path: str | Path) -> PenPreset:
    """Read at most the format byte limit before parsing or decoding a tip."""
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_PRESET_BYTES + 1)
    if len(raw) > MAX_PRESET_BYTES:
        raise ValueError("LoomStroke pen file exceeds the byte limit")
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid LoomStroke pen JSON") from exc
    return PenPreset.from_dict(document)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def save_pen_preset(preset: PenPreset, path: str | Path) -> Path:
    """Keep the previous destination intact if writing or replacing fails."""
    raw = json.dumps(preset.to_dict(), ensure_ascii=False, indent=2, allow_nan=False).encode(
        "utf-8"
    )
    if len(raw) > MAX_PRESET_BYTES:
        raise ValueError("LoomStroke pen file exceeds the byte limit")
    destination = Path(path)
    _atomic_write(destination, raw)
    return destination


class PenPresetStore:
    """A separate pens library inside the existing basic_pen plugin data root."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = Path(root_dir) if root_dir is not None else plugin_data_dir("basic_pen")
        self.pens_dir = self.root_dir / "pens"

    def _preset_path(self, preset_id: str) -> Path:
        return self.pens_dir / f"{validate_preset_id(preset_id)}{PEN_EXTENSION}"

    def list_presets(self) -> list[PenPreset]:
        if not self.pens_dir.exists():
            return []
        presets = []
        for path in sorted(self.pens_dir.glob(f"*{PEN_EXTENSION}")):
            try:
                preset = load_pen_preset(path)
                if path.stem != preset.id:
                    raise ValueError("Pen preset UUID does not match its library filename")
                presets.append(preset)
            except (OSError, ValueError):
                logger.warning("Could not load pen preset %s; original file retained", path.name)
        return sorted(presets, key=lambda preset: (preset.name.casefold(), preset.id))

    def get_preset(self, preset_id: str) -> PenPreset | None:
        path = self._preset_path(preset_id)
        if not path.exists():
            return None
        preset = load_pen_preset(path)
        if preset.id != preset_id:
            raise ValueError("Pen preset UUID does not match its library filename")
        return preset

    def save_preset(self, preset: PenPreset) -> Path:
        return save_pen_preset(preset, self._preset_path(preset.id))

    def delete_preset(self, preset_id: str) -> None:
        self._preset_path(preset_id).unlink(missing_ok=True)

    def import_preset(self, path: str | Path) -> PenPreset:
        preset = replace(load_pen_preset(path), id=str(uuid.uuid4()))
        self.save_preset(preset)
        return preset

    def export_preset(self, preset: PenPreset, path: str | Path) -> Path:
        return save_pen_preset(preset, path)

    def materialize_tip(self, preset: PenPreset) -> Path | None:
        """Expand the validated embedded tip to a content-addressed local cache."""
        if preset.tip_png is None:
            return None
        digest = hashlib.sha256(preset.tip_png).hexdigest()
        path = self.pens_dir / "tips" / f"{digest}.png"
        if (
            not path.exists()
            or path.stat().st_size != len(preset.tip_png)
            or path.read_bytes() != preset.tip_png
        ):
            _atomic_write(path, preset.tip_png)
        return path
