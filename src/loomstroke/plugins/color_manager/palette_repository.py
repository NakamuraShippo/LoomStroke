"""Palette persistence utilities for the color manager plugin."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from loomstroke.core.color_state import ColorDescriptor
from loomstroke.platform.cache_paths import get_cache_root
from loomstroke.plugins.color_manager.color_models import (
    DEFAULT_PALETTE_ID,
    ColorPalette,
    PaletteSwatch,
)
from loomstroke.plugins.color_manager.errors import PaletteExportError, PaletteImportError


class PaletteFormat(StrEnum):
    """Supported palette interchange formats."""

    JSON = "json"
    ACO = "aco"
    ASE = "ase"
    GPL = "gpl"


@dataclass
class SettingsGateway:
    """Minimal interface used for repository persistence."""

    settings: Any

    def get_path(self, key: str, default: Path) -> Path:
        value = getattr(self.settings, "get", lambda *_: None)(key, None)
        if value:
            return Path(str(value))
        return default

    def set_path(self, key: str, value: Path) -> None:
        setter = getattr(self.settings, "set", None)
        if callable(setter):
            setter(key, str(value))


class ColorPaletteRepository:
    """Async-aware repository for palette serialization."""

    def __init__(self, settings: Any, *, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._settings = SettingsGateway(settings)
        self._loop = loop
        self._palettes: dict[str, ColorPalette] = {}
        self._lock = asyncio.Lock()
        default_path = get_cache_root() / "palettes.json"
        self._storage_path = self._settings.get_path("color/palette_path", default_path)
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)

    async def load(self) -> None:
        should_save_default = False
        async with self._lock:
            if not self._storage_path.exists():
                self._palettes = {DEFAULT_PALETTE_ID: _build_default_palette()}
                should_save_default = True
            else:
                data = await asyncio.to_thread(self._storage_path.read_text, encoding="utf-8")
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise PaletteImportError(f"Invalid palette file: {exc}") from exc
                palettes = [ColorPalette.from_dict(item) for item in payload.get("palettes", [])]
                if not palettes:
                    palettes = [_build_default_palette()]
                self._palettes = {palette.id: palette for palette in palettes}
        if should_save_default:
            await self.save()

    async def save(self) -> None:
        async with self._lock:
            payload = {"palettes": [palette.to_dict() for palette in self._palettes.values()]}
            await asyncio.to_thread(
                self._storage_path.write_text,
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )
            self._settings.set_path("color/palette_path", self._storage_path)

    async def list_palettes(self) -> list[ColorPalette]:
        async with self._lock:
            return list(self._palettes.values())

    async def get_palette(self, palette_id: str) -> ColorPalette | None:
        async with self._lock:
            return self._palettes.get(palette_id)

    async def upsert_palette(self, palette: ColorPalette) -> None:
        async with self._lock:
            self._palettes[palette.id] = palette
        await self.save()

    async def delete_palette(self, palette_id: str) -> None:
        async with self._lock:
            self._palettes.pop(palette_id, None)
        await self.save()

    async def import_file(self, path: Path, *, fmt: PaletteFormat) -> list[ColorPalette]:
        if fmt is PaletteFormat.JSON:
            data = await asyncio.to_thread(path.read_text, encoding="utf-8")
            payload = json.loads(data)
            palettes = [ColorPalette.from_dict(item) for item in payload.get("palettes", [])]
            if not palettes:
                raise PaletteImportError("No palettes found in JSON payload")
            return palettes
        raise PaletteImportError(f"Format {fmt} is not implemented yet")

    async def export_palette(
        self, palette_id: str, dest: Path, *, fmt: PaletteFormat = PaletteFormat.JSON
    ) -> None:
        palette = await self.get_palette(palette_id)
        if palette is None:
            raise PaletteExportError(f"Palette {palette_id} not found")
        if fmt is PaletteFormat.JSON:
            payload = {"palettes": [palette.to_dict()]}
            await asyncio.to_thread(dest.write_text, json.dumps(payload, indent=2), "utf-8")
            return
        raise PaletteExportError(f"Format {fmt} is not implemented yet")


def _build_default_palette() -> ColorPalette:
    swatches = [
        PaletteSwatch(
            id=f"swatch_{index}",
            label=name,
            color=ColorDescriptor(rgba=rgba, blend_mode="normal"),
        )
        for index, (name, rgba) in enumerate(
            (
                ("Black", (0.0, 0.0, 0.0, 1.0)),
                ("White", (1.0, 1.0, 1.0, 1.0)),
                ("Red", (1.0, 0.0, 0.0, 1.0)),
                ("Green", (0.0, 1.0, 0.0, 1.0)),
                ("Blue", (0.0, 0.0, 1.0, 1.0)),
                ("Cyan", (0.0, 1.0, 1.0, 1.0)),
                ("Magenta", (1.0, 0.0, 1.0, 1.0)),
                ("Yellow", (1.0, 1.0, 0.0, 1.0)),
            )
        )
    ]
    return ColorPalette(id=DEFAULT_PALETTE_ID, name="Default", swatches=swatches)


__all__ = [
    "ColorPaletteRepository",
    "PaletteFormat",
]
