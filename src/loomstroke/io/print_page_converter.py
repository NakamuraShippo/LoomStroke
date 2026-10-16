"""One rendered page -> one print TIFF (CT-11 P3, spec 11 §8.1 / §9.2).

The converter is configured once per export (output mode, source and
destination profiles, intent, BPC) and validated **at construction** —
a CMYK output without a real CMYK profile is refused before any page is
rendered, never discovered half way through a sequence (spec 11 §14).
Every colour decision stays inside :mod:`print_color_output`, i.e.
LittleCMS with registered, fingerprint-addressed profiles.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

import numpy as np
from numpy.typing import NDArray

from .color_profile_registry import ColorProfileRegistry, RenderingIntent
from .native_cmyk_print_output import NativeCmykPrintOutput
from .print_color_output import (
    rgba_to_cmyk,
    rgba_to_grayscale_k,
    write_cmyk_tiff,
    write_grayscale_k_tiff,
)

__all__ = ["PrintOutput", "PrintPageConverter"]

PrintOutput = Literal["cmyk_tiff", "grayscale_k_tiff"]


class PrintPageConverter:
    """Flatten + separate one page and write it atomically as a TIFF."""

    OUTPUTS: ClassVar[tuple[str, ...]] = ("cmyk_tiff", "grayscale_k_tiff")
    EXTENSION: ClassVar[str] = "tif"

    def __init__(
        self,
        registry: ColorProfileRegistry,
        *,
        output: str,
        source_profile_id: str,
        cmyk_profile_id: str | None = None,
        intent: RenderingIntent = "relative",
        black_point_compensation: bool = True,
    ) -> None:
        if output not in self.OUTPUTS:
            raise ValueError(f"unsupported print output: {output}")
        if registry.profile_bytes(source_profile_id) is None:
            raise ValueError(f"unknown source profile: {source_profile_id}")
        self._registry = registry
        self._output = output
        self._source_profile_id = source_profile_id
        self._intent: RenderingIntent = intent
        self._black_point_compensation = bool(black_point_compensation)
        self._cmyk_profile_id: str | None = None
        self._cmyk_bytes: bytes | None = None
        if output == "cmyk_tiff":
            self._cmyk_profile_id, self._cmyk_bytes = self._require_cmyk(registry, cmyk_profile_id)

    @staticmethod
    def _require_cmyk(registry: ColorProfileRegistry, profile_id: str | None) -> tuple[str, bytes]:
        if not profile_id:
            raise ValueError("CMYK output requires a CMYK profile")
        info = registry.info(profile_id)
        payload = registry.profile_bytes(profile_id)
        if info is None or payload is None:
            raise ValueError(f"unknown CMYK profile: {profile_id}")
        if info.colorspace != "CMYK":
            raise ValueError(f"profile {info.name!r} is {info.colorspace}, not CMYK")
        return profile_id, payload

    @property
    def output(self) -> str:
        return self._output

    @property
    def cmyk_profile_id(self) -> str | None:
        return self._cmyk_profile_id

    @property
    def embeds_profile(self) -> bool:
        """CMYK output carries the destination profile; K ink has none (§9.2)."""
        return self._output == "cmyk_tiff"

    def write_page(
        self,
        rgba: NDArray[np.uint8],
        destination: Path,
        *,
        dpi: tuple[float, float],
    ) -> int:
        """Convert straight-alpha RGBA8 and write ``destination`` atomically.

        Returns the number of bytes written.
        """
        rgba = np.ascontiguousarray(rgba)
        if self._output == "cmyk_tiff":
            assert self._cmyk_profile_id is not None and self._cmyk_bytes is not None
            cmyk = rgba_to_cmyk(
                rgba,
                self._registry,
                source_profile_id=self._source_profile_id,
                cmyk_profile_id=self._cmyk_profile_id,
                intent=self._intent,
                black_point_compensation=self._black_point_compensation,
            )
            return write_cmyk_tiff(Path(destination), cmyk, dpi=dpi, icc_bytes=self._cmyk_bytes)
        k_plane = rgba_to_grayscale_k(
            rgba, self._registry, source_profile_id=self._source_profile_id
        )
        return write_grayscale_k_tiff(Path(destination), k_plane, dpi=dpi)

    def write_native_page(self, pixels, destination, *, dpi):
        if self._output != "cmyk_tiff" or self._cmyk_bytes is None:
            raise ValueError("Native ink output requires CMYK TIFF")
        return NativeCmykPrintOutput.write(
            pixels,
            destination,
            profile=self._cmyk_bytes,
            dpi=dpi,
            intent=self._intent,
            black_point_compensation=self._black_point_compensation,
        )
