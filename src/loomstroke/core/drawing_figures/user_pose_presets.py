"""Persistent, named snapshots of drawing figure poses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from loomstroke.io.atomic_export_writer import write_atomically
from loomstroke.platform.user_dirs import default_user_root

from .model import FigurePose


@dataclass(frozen=True)
class SavedFigurePose:
    id: str
    name: str
    pose: FigurePose


class FigurePosePresetStore:
    """Keep pose snapshots separate from document and page-template formats."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (
            path
            if path is not None
            else default_user_root() / "settings" / "drawing_figure_pose_presets.json"
        )

    def list(self, model: str | None = None) -> tuple[SavedFigurePose, ...]:
        values = self._load()
        return tuple(value for value in values if model is None or value.pose.model == model)

    def get(self, preset_id: str) -> SavedFigurePose | None:
        return next((item for item in self._load() if item.id == preset_id), None)

    def save(self, name: str, pose: FigurePose) -> SavedFigurePose:
        name = self._name(name)
        snapshot = FigurePose.from_dict(pose.to_dict())
        values = list(self._load())
        if any(
            item.pose.model == snapshot.model and item.name.casefold() == name.casefold()
            for item in values
        ):
            raise ValueError("A pose preset with this name already exists for this model")
        saved = SavedFigurePose(uuid4().hex, name, snapshot)
        self._write((*values, saved))
        return saved

    def delete(self, preset_id: str) -> bool:
        values = self._load()
        kept = tuple(item for item in values if item.id != preset_id)
        if len(values) == len(kept):
            return False
        self._write(kept)
        return True

    @staticmethod
    def _name(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("Invalid pose preset name")
        name = value.strip()
        if not name or len(name) > 80 or any(ord(char) < 32 for char in name):
            raise ValueError("Invalid pose preset name")
        return name

    def _load(self) -> tuple[SavedFigurePose, ...]:
        if not self.path.exists():
            return ()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise ValueError("Unsupported pose preset library")
            entries = payload.get("presets")
            if not isinstance(entries, list):
                raise ValueError("Invalid pose preset library")
            result: list[SavedFigurePose] = []
            ids: set[str] = set()
            names: set[tuple[str, str]] = set()
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("Invalid pose preset entry")
                preset_id = entry.get("id")
                if not isinstance(preset_id, str) or not preset_id or preset_id in ids:
                    raise ValueError("Invalid or duplicate pose preset ID")
                name = self._name(entry.get("name"))
                pose = FigurePose.from_dict(entry.get("pose"))
                key = (pose.model, name.casefold())
                if key in names:
                    raise ValueError("Duplicate pose preset name")
                ids.add(preset_id)
                names.add(key)
                result.append(SavedFigurePose(preset_id, name, pose))
            return tuple(result)
        except (json.JSONDecodeError, TypeError, OverflowError) as error:
            raise ValueError("Invalid pose preset library") from error

    def _write(self, values: tuple[SavedFigurePose, ...]) -> None:
        payload = {
            "version": 1,
            "presets": [
                {"id": item.id, "name": item.name, "pose": item.pose.to_dict()} for item in values
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        write_atomically(
            self.path, lambda temporary: temporary.write_text(encoded, encoding="utf-8")
        )
