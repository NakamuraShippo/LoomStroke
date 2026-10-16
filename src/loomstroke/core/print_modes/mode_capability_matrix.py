"""Capabilities of document storage, editable channels and print output."""

from __future__ import annotations

from typing import ClassVar

from .mode_capability import ModeCapability

__all__ = ["ModeCapabilityMatrix"]


class ModeCapabilityMatrix:
    _ROWS: ClassVar[tuple[ModeCapability, ...]] = (
        ModeCapability(
            "rgba8",
            8,
            ("r", "g", "b", "a"),
            True,
            True,
            True,
            True,
            True,
            True,
            "rgba8unorm",
            "QImage.Format_ARGB32_Premultiplied (current editing truth)",
        ),
        ModeCapability(
            "gray8",
            8,
            ("k",),
            True,
            False,
            False,
            False,
            False,
            False,
            "r8unorm",
            "D2: storage / brush / golden not implemented",
        ),
        ModeCapability(
            "mono1",
            1,
            ("k",),
            False,
            False,
            False,
            False,
            False,
            False,
            "r8unorm",
            "D2: bit-packed plane (packer available), no storage yet",
        ),
        ModeCapability(
            "rgba16",
            16,
            ("r", "g", "b", "a"),
            True,
            False,
            False,
            False,
            False,
            False,
            "rgba16unorm",
            "D3: no 16-bit storage / upload",
        ),
        ModeCapability(
            "cmyk8",
            8,
            ("c", "m", "y", "k", "a"),
            True,
            True,
            True,
            True,
            True,
            True,
            "storage_buffer_f32",
            "Native ICC CMYK; normal paint, fill, gradient, erase; raster layers; 24 blends",
        ),
        ModeCapability(
            "cmyk16",
            16,
            ("c", "m", "y", "k", "a"),
            True,
            True,
            True,
            True,
            True,
            True,
            "storage_buffer_f32",
            "Lossless 16-bit plates; RGBA8 ICC display; TIFF uses the document ICC profile",
        ),
    )

    def get(self, mode_id: str) -> ModeCapability | None:
        return next((row for row in self._ROWS if row.mode_id == mode_id), None)

    def rows(self) -> tuple[ModeCapability, ...]:
        return self._ROWS

    def editable_modes(self) -> tuple[str, ...]:
        return tuple(row.mode_id for row in self._ROWS if row.editable)

    def estimate_bytes(self, mode_id: str, width: int, height: int, layers: int = 1) -> int:
        capability = self.get(mode_id)
        if capability is None:
            return 0
        pixels = max(0, int(width)) * max(0, int(height)) * max(1, int(layers))
        return int(pixels * capability.bytes_per_pixel)
