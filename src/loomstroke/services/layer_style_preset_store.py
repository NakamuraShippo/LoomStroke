"""Bounded, locked and atomic storage within the existing user settings directory."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QIODevice, QLockFile, QSaveFile

from loomstroke.core.appearance.layer_style_preset import LayerStylePreset
from loomstroke.core.appearance.layer_style_stack import LayerStyleStack
from loomstroke.platform.user_dirs import default_user_root


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate preset library key")
        result[key] = value
    return result


class LayerStylePresetStore:
    MAX_BYTES = 1024 * 1024
    MAX_PRESETS = 256

    def __init__(self, path: Path | None = None) -> None:
        self.path = (
            Path(path)
            if path is not None
            else default_user_root(create=False) / "settings" / "layer_style_presets.json"
        )

    def list_presets(self) -> list[LayerStylePreset]:
        if not self.path.exists():
            return []
        with self.path.open("rb") as stream:
            raw = stream.read(self.MAX_BYTES + 1)
        if len(raw) > self.MAX_BYTES:
            raise ValueError("Preset library is too large")
        try:
            value = json.loads(
                raw.decode("utf-8"),
                parse_constant=self._reject_constant,
                object_pairs_hook=_unique_keys,
            )
            if not isinstance(value, dict) or value.get("version") != 1:
                raise ValueError("Unsupported preset library")
            entries = value.get("presets")
            if not isinstance(entries, list) or len(entries) > self.MAX_PRESETS:
                raise ValueError("Invalid preset library")
            presets = [LayerStylePreset.from_dict(entry) for entry in entries]
            if len({p.preset_id for p in presets}) != len(presets):
                raise ValueError("Duplicate preset IDs")
            return sorted(presets, key=lambda p: (p.name.casefold(), p.preset_id))
        except (UnicodeError, RecursionError, TypeError) as exc:
            raise ValueError("Invalid preset library") from exc

    @staticmethod
    def _reject_constant(value: str) -> Any:
        raise ValueError(f"Invalid preset number: {value}")

    def add(self, name: str, stack: LayerStyleStack) -> LayerStylePreset:
        preset = LayerStylePreset.from_dict(
            {"id": str(uuid4()), "name": name, "stack": stack.to_dict()}
        )
        self._mutate(lambda values: values.append(preset))
        return preset

    def update(
        self, identifier: str, *, name: str | None = None, stack: LayerStyleStack | None = None
    ) -> None:
        def change(values: list[LayerStylePreset]) -> None:
            index = next((i for i, p in enumerate(values) if p.preset_id == identifier), None)
            if index is None:
                raise ValueError("Preset no longer exists")
            data = values[index].to_dict()
            if name is not None:
                data["name"] = name
            if stack is not None:
                data["stack"] = stack.to_dict()
            values[index] = LayerStylePreset.from_dict(data)

        self._mutate(change)

    def delete(self, identifier: str) -> None:
        def change(values: list[LayerStylePreset]) -> None:
            values[:] = [p for p in values if p.preset_id != identifier]

        self._mutate(change)

    def _mutate(self, change: Callable[[list[LayerStylePreset]], None]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(self.path) + ".lock")
        if not lock.tryLock(0):
            raise OSError("Preset library is in use")
        try:
            values = self.list_presets()
            change(values)
            if len(values) > self.MAX_PRESETS:
                raise ValueError("Too many presets")
            raw = json.dumps(
                {"version": 1, "presets": [p.to_dict() for p in values]},
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ).encode("utf-8")
            if len(raw) > self.MAX_BYTES:
                raise ValueError("Preset library is too large")
            output = QSaveFile(str(self.path))
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            if output.write(raw) != len(raw):
                output.cancelWriting()
                raise OSError(output.errorString())
            if not output.commit():
                raise OSError(output.errorString())
        finally:
            lock.unlock()
