"""Dimension math and validation for Image Size (CT-13 spec §5, §15).

Pure and Qt-free: the dialog, the canvas service and the tests all go
through this one class so that rounding (half-up) and the unit formulas
never drift apart between preview, apply and undo.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from .image_size_target import ImageSizeTarget
from .resample_plan import PageResamplePlan


class ImageSizePlanner:
    """Validate and derive pixel / physical / DPI values."""

    MIN_DIMENSION = 1
    #: Matches the canvas resize dialog's upper bound and the IO safety limits.
    MAX_DIMENSION = 16384
    MIN_DPI = 1
    MM_PER_INCH = 25.4
    #: Bytes per pixel of the premultiplied RGBA layer buffers.
    BYTES_PER_PIXEL = 4

    # -- rounding -------------------------------------------------------------

    @staticmethod
    def round_half_up(value: float) -> int:
        """Round *value* half-up to an int (the single rounding rule, spec §5)."""
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("cannot round a non-finite value")
        return math.floor(number + 0.5)

    # -- validation -----------------------------------------------------------

    def validate_dimensions(self, width: object, height: object) -> tuple[str, ...]:
        """Return stable issue codes for a pixel size (empty when valid)."""
        issues: list[str] = []
        issues.extend(self._dimension_issues(width, "width"))
        issues.extend(self._dimension_issues(height, "height"))
        return tuple(issues)

    def validate_dpi(self, dpi: object) -> tuple[str, ...]:
        if isinstance(dpi, bool) or not isinstance(dpi, (int, float)):
            return ("invalid_dpi",)
        if not math.isfinite(float(dpi)):
            return ("invalid_dpi",)
        if float(dpi) < self.MIN_DPI:
            return ("invalid_dpi",)
        return ()

    def validate_target(self, target: ImageSizeTarget) -> tuple[str, ...]:
        issues = list(self.validate_dimensions(target.width_px, target.height_px))
        issues.extend(self.validate_dpi(target.dpi))
        return tuple(issues)

    def _dimension_issues(self, value: object, axis: str) -> list[str]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"invalid_{axis}"]
        number = float(value)
        if not math.isfinite(number) or number < self.MIN_DIMENSION:
            return [f"invalid_{axis}"]
        if number > self.MAX_DIMENSION:
            return [f"{axis}_exceeds_limit"]
        return []

    # -- unit conversion ------------------------------------------------------

    def inches(self, pixels: float, dpi: float) -> float:
        return float(pixels) / max(float(dpi), float(self.MIN_DPI))

    def millimetres(self, pixels: float, dpi: float) -> float:
        return self.inches(pixels, dpi) * self.MM_PER_INCH

    def pixels_for_inches(self, inches: float, dpi: float) -> int:
        return max(self.MIN_DIMENSION, self.round_half_up(float(inches) * float(dpi)))

    def pixels_for_millimetres(self, millimetres: float, dpi: float) -> int:
        return self.pixels_for_inches(float(millimetres) / self.MM_PER_INCH, dpi)

    def dpi_for_inches(self, pixels: float, inches: float) -> float:
        """DPI that makes *pixels* span *inches* (Resample OFF physical edit)."""
        width_in = float(inches)
        if width_in <= 0.0 or not math.isfinite(width_in):
            raise ValueError("physical size must be positive")
        return float(pixels) / width_in

    def pixels_for_percent(self, pixels: float, percent: float) -> int:
        return max(self.MIN_DIMENSION, self.round_half_up(float(pixels) * float(percent) / 100.0))

    def percent_for_pixels(self, pixels: float, source_pixels: float) -> float:
        source = float(source_pixels)
        if source <= 0.0:
            return 100.0
        return float(pixels) / source * 100.0

    # -- aspect ratio ---------------------------------------------------------

    def constrained_height(self, width: float, source_width: float, source_height: float) -> int:
        if float(source_width) <= 0.0:
            return max(self.MIN_DIMENSION, self.round_half_up(width))
        return max(
            self.MIN_DIMENSION,
            self.round_half_up(float(width) * float(source_height) / float(source_width)),
        )

    def constrained_width(self, height: float, source_width: float, source_height: float) -> int:
        if float(source_height) <= 0.0:
            return max(self.MIN_DIMENSION, self.round_half_up(height))
        return max(
            self.MIN_DIMENSION,
            self.round_half_up(float(height) * float(source_width) / float(source_height)),
        )

    # -- planning -------------------------------------------------------------

    def scale_factors(
        self,
        source_size: Sequence[int],
        target_size: Sequence[int],
    ) -> tuple[float, float]:
        source_w, source_h = int(source_size[0]), int(source_size[1])
        target_w, target_h = int(target_size[0]), int(target_size[1])
        if source_w <= 0 or source_h <= 0:
            raise ValueError("source size must be positive")
        return (target_w / source_w, target_h / source_h)

    def estimate_history_bytes(
        self,
        width: int,
        height: int,
        layer_count: int,
        *,
        source_size: Sequence[int] | None = None,
    ) -> int:
        """Rough retained-undo estimate: before + after buffers of every layer."""
        layers = max(1, int(layer_count))
        after = max(0, int(width)) * max(0, int(height)) * self.BYTES_PER_PIXEL
        before = after
        if source_size is not None:
            before = (
                max(0, int(source_size[0])) * max(0, int(source_size[1])) * self.BYTES_PER_PIXEL
            )
        return (before + after) * layers

    def plan(
        self,
        *,
        source_width: int,
        source_height: int,
        source_dpi: int,
        target: ImageSizeTarget,
    ) -> PageResamplePlan:
        """Validate *target* against the source and freeze the scale factors.

        Raises ``ValueError`` (message = comma separated issue codes) when the
        target is invalid, so callers never resample with bad numbers.
        """
        issues = list(self.validate_dimensions(source_width, source_height))
        issues = [code.replace("invalid_", "invalid_source_") for code in issues]
        issues.extend(self.validate_target(target))
        if issues:
            raise ValueError(",".join(issues))
        if target.resample:
            target_size = (int(target.width_px), int(target.height_px))
        else:
            target_size = (int(source_width), int(source_height))
            target = ImageSizeTarget(
                width_px=target_size[0],
                height_px=target_size[1],
                dpi=int(target.dpi),
                interpolation=target.interpolation,
                resample=False,
            )
        scale = self.scale_factors((source_width, source_height), target_size)
        return PageResamplePlan(
            source_size=(int(source_width), int(source_height)),
            source_dpi=int(source_dpi),
            target=target,
            scale=scale,
        )
