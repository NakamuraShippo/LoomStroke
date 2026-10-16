"""Palette import/export helpers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from loomstroke.plugins.color_manager.color_models import ColorPalette
from loomstroke.plugins.color_manager.errors import PaletteExportError, PaletteImportError


async def load_aco(path: Path) -> list[ColorPalette]:
    """Load Adobe Color (ACO) palettes."""
    raise PaletteImportError("ACO import not implemented")


async def save_aco(palettes: Sequence[ColorPalette], path: Path) -> None:
    """Save palettes to Adobe Color (ACO) format."""
    raise PaletteExportError("ACO export not implemented")


async def load_ase(path: Path) -> list[ColorPalette]:
    raise PaletteImportError("ASE import not implemented")


async def save_ase(palettes: Sequence[ColorPalette], path: Path) -> None:
    raise PaletteExportError("ASE export not implemented")


async def load_gpl(path: Path) -> list[ColorPalette]:
    raise PaletteImportError("GPL import not implemented")


async def save_gpl(palettes: Sequence[ColorPalette], path: Path) -> None:
    raise PaletteExportError("GPL export not implemented")


__all__ = [
    "load_aco",
    "load_ase",
    "load_gpl",
    "save_aco",
    "save_ase",
    "save_gpl",
]
