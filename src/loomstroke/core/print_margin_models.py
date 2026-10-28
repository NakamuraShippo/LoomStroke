"""Session-only margin information and immutable point-coordinate primitives."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PrintMarginOptions:
    file_name: bool = False
    page_number: bool = False
    date_time: bool = False
    color_mode: bool = False
    color_bar: bool = False
    gray_bar: bool = False

    @property
    def enabled(self) -> bool:
        return any(
            (
                self.file_name,
                self.page_number,
                self.date_time,
                self.color_mode,
                self.color_bar,
                self.gray_bar,
            )
        )


@dataclass(frozen=True, slots=True)
class PrintMarginContext:
    file_name: str = ""
    page_number: int = 1
    total_pages: int = 1
    date_time: str = ""
    color_mode: str = "RGB"


@dataclass(frozen=True, slots=True)
class PrintMarginGeometry:
    text: str = ""
    text_rect_pt: tuple[float, float, float, float] | None = None
    # x/y/w/h and the four process CMYK fractions for each swatch.
    swatches: tuple[
        tuple[tuple[float, float, float, float], tuple[float, float, float, float]], ...
    ] = ()
