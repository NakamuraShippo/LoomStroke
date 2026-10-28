from __future__ import annotations

"""Custom exceptions for the color manager plugin."""


class PaletteImportError(Exception):
    """Raised when palette import fails."""


class PaletteExportError(Exception):
    """Raised when palette export fails."""


class PaletteSettingsUpdateError(PaletteExportError):
    """The palette file committed, but its location setting did not update."""

    file_committed = True
    settings_updated = False

    def __init__(self) -> None:
        super().__init__("Palette saved, but its location setting could not be updated.")


__all__ = ["PaletteExportError", "PaletteImportError", "PaletteSettingsUpdateError"]
