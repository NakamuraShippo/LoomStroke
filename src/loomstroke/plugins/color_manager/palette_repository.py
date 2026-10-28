"""Palette persistence utilities for the color manager plugin."""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
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
from loomstroke.plugins.color_manager.errors import (
    PaletteExportError,
    PaletteImportError,
    PaletteSettingsUpdateError,
)

from .palette_persistence import read_palette_document, write_palette_document

logger = logging.getLogger(__name__)


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
        self._loaded = False
        self._load_failed = False
        self._settings_warning = False
        default_path = get_cache_root() / "palettes.json"
        self._storage_path = self._settings.get_path("color/palette_path", default_path)
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)

    async def load(self) -> None:
        async with self._lock:
            self._loaded = False
            self._load_failed = True
            if not self._source_exists():
                await self._persist_locked({DEFAULT_PALETTE_ID: _build_default_palette()})
                return
            candidate = await asyncio.to_thread(read_palette_document, self._storage_path)
            self._palettes = candidate or {DEFAULT_PALETTE_ID: _build_default_palette()}
            self._loaded = True
            self._load_failed = False

    async def save(self) -> None:
        async with self._lock:
            self._require_writable()
            await self._persist_locked(deepcopy(self._palettes))

    async def list_palettes(self) -> list[ColorPalette]:
        async with self._lock:
            return deepcopy(list(self._palettes.values()))

    async def get_palette(self, palette_id: str) -> ColorPalette | None:
        async with self._lock:
            return deepcopy(self._palettes.get(palette_id))

    async def upsert_palette(self, palette: ColorPalette) -> None:
        incoming = deepcopy(palette)
        async with self._lock:
            self._require_writable()
            candidate = deepcopy(self._palettes)
            candidate[incoming.id] = incoming
            await self._persist_locked(candidate)

    async def delete_palette(self, palette_id: str) -> None:
        async with self._lock:
            self._require_writable()
            candidate = deepcopy(self._palettes)
            candidate.pop(palette_id, None)
            await self._persist_locked(candidate)

    async def import_file(self, path: Path, *, fmt: PaletteFormat) -> list[ColorPalette]:
        if fmt is PaletteFormat.JSON:
            palettes = list((await asyncio.to_thread(read_palette_document, path)).values())
            if not palettes:
                raise PaletteImportError("No palettes found in JSON payload")
            return palettes
        raise PaletteImportError(f"Format {fmt} is not implemented yet")

    async def export_palette(
        self, palette_id: str, dest: Path, *, fmt: PaletteFormat = PaletteFormat.JSON
    ) -> None:
        async with self._lock:
            self._reject_store_export(Path(dest))
            palette = self._palettes.get(palette_id)
            if palette is None:
                raise PaletteExportError(f"Palette {palette_id} not found")
            if fmt is not PaletteFormat.JSON:
                raise PaletteExportError(f"Format {fmt} is not implemented yet")
            payload = {"palettes": [deepcopy(palette).to_dict()]}
            _verified, cancellation = await self._write_locked(Path(dest), payload)
            if cancellation is not None:
                raise cancellation

    def has_settings_warning(self) -> bool:
        return self._settings_warning

    def take_settings_warning(self) -> bool:
        pending = self._settings_warning
        self._settings_warning = False
        return pending

    def _source_exists(self) -> bool:
        try:
            self._storage_path.lstat()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise PaletteImportError(
                "The palette file could not be checked before saving."
            ) from error
        return True

    def _require_writable(self) -> None:
        if self._load_failed or (not self._loaded and self._source_exists()):
            raise PaletteImportError("Load the existing palette file successfully before saving.")

    def _reject_store_export(self, destination: Path) -> None:
        try:
            alias = destination.resolve() == self._storage_path.resolve()
            if not alias and destination.exists() and self._source_exists():
                alias = destination.samefile(self._storage_path)
        except (OSError, RuntimeError, ValueError) as error:
            raise PaletteExportError(
                "The palette export destination could not be verified."
            ) from error
        if alias:
            raise PaletteExportError("A single-palette export cannot replace the palette store.")

    async def _write_locked(
        self, destination: Path, payload: dict[str, Any]
    ) -> tuple[dict[str, ColorPalette], asyncio.CancelledError | None]:
        worker = asyncio.create_task(
            asyncio.to_thread(write_palette_document, destination, payload)
        )
        cancellation = None
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError as error:
                cancellation = error
            except Exception:
                break
        try:
            verified = worker.result()
        except Exception:
            if cancellation is not None:
                logger.warning("Cancelled palette writer failed; previous palette remains active")
                raise cancellation from None
            raise
        return verified, cancellation

    async def _persist_locked(self, candidate: dict[str, ColorPalette]) -> None:
        payload = {"palettes": [palette.to_dict() for palette in candidate.values()]}
        verified, cancellation = await self._write_locked(self._storage_path, payload)
        self._palettes = verified
        self._loaded = True
        self._load_failed = False
        settings_failed = False
        try:
            self._settings.set_path("color/palette_path", self._storage_path)
        except Exception:
            settings_failed = True
            logger.warning("Palette location setting failed after the file was saved")
        self._settings_warning = settings_failed
        if cancellation is not None:
            raise cancellation
        if settings_failed:
            raise PaletteSettingsUpdateError()


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
