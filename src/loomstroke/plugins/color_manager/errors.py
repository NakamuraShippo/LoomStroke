from __future__ import annotations

"""Custom exceptions for the color manager plugin."""


class PaletteImportError(Exception):
    """Raised when palette import fails."""


class PaletteExportError(Exception):
    """Raised when palette export fails."""


__all__ = ["PaletteExportError", "PaletteImportError"]
