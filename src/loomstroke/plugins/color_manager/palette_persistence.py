"""Read/verify the existing palette JSON before atomically replacing a store."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .color_models import ColorPalette
from .errors import PaletteExportError, PaletteImportError


def decode_palette_text(text: str) -> dict[str, ColorPalette]:
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict) or (payload and "palettes" not in payload):
            raise ValueError("Invalid palette document")
        rows = payload.get("palettes", [])
        if not isinstance(rows, list):
            raise ValueError("Invalid palette list")
        palettes = {}
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("Invalid palette entry")
            palette = ColorPalette.from_dict(row)
            if palette.id in palettes:
                raise ValueError("Duplicate palette identifier")
            palettes[palette.id] = palette
        return palettes
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError) as error:
        raise PaletteImportError("The palette file contains invalid palette data.") from error


def read_palette_document(path: Path) -> dict[str, ColorPalette]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PaletteImportError("The palette file could not be read.") from error
    return decode_palette_text(text)


def _meaning(palettes: dict[str, ColorPalette]) -> str:
    # Canonical JSON also compares supported nonfinite legacy metadata stably.
    return json.dumps([palette.to_dict() for palette in palettes.values()], sort_keys=True)


def write_palette_document(path: Path, payload: dict[str, Any]) -> dict[str, ColorPalette]:
    from loomstroke.io.atomic_export_writer import write_atomically  # noqa: PLC0415

    text = json.dumps(payload, indent=2)
    try:
        expected = decode_palette_text(text)
    except PaletteImportError as error:
        raise PaletteExportError("The palette data could not be verified for saving.") from error
    verified: dict[str, ColorPalette] = {}

    def encode(temporary: Path) -> None:
        nonlocal verified
        with temporary.open("w", encoding="utf-8") as stream:
            if stream.write(text) != len(text):
                raise OSError("The palette write was incomplete.")
            stream.flush()
            os.fsync(stream.fileno())
        actual_text = temporary.read_text(encoding="utf-8")
        if actual_text != text:
            raise PaletteExportError("The saved palette did not match the requested data.")
        try:
            verified = decode_palette_text(actual_text)
        except PaletteImportError as error:
            raise PaletteExportError("The saved palette could not be read back.") from error
        if _meaning(verified) != _meaning(expected):
            raise PaletteExportError("The saved palette changed during verification.")

    write_atomically(path, encode)
    return verified
