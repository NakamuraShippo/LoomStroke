"""Pure geometry for numeric and batch guide creation (canvas pixels)."""

from __future__ import annotations

from math import isfinite
from typing import Literal

GuideAxis = Literal["horizontal", "vertical"]
GuidePosition = tuple[GuideAxis, float]
MAX_DIVISIONS = 1000


def canvas_snap_positions(extent: float, *, edges: bool, center: bool) -> list[float]:
    """Candidates in legacy priority order; center is opt-in."""
    positions = [0.0, float(extent)] if edges else []
    if center:
        positions.append(extent / 2)
    return positions


def map_guide_positions(
    positions: list[GuidePosition],
    source_size: tuple[float, float],
    target_size: tuple[float, float],
    *,
    proportional: bool,
) -> list[GuidePosition]:
    """Map a template without rounding and validate all target positions first."""
    if any(not isfinite(v) or v <= 0 for v in (*source_size, *target_size)):
        raise ValueError("Invalid template canvas size")
    result = []
    for orientation, value in positions:
        numeric_guide(orientation, value, unit="px", dpi=(72, 72), size=source_size)
        axis = 0 if orientation == "vertical" else 1
        mapped = value * target_size[axis] / source_size[axis] if proportional else value
        result.append(numeric_guide(orientation, mapped, unit="px", dpi=(72, 72), size=target_size))
    return result


def unit_to_pixels(value: float, unit: str, dpi: float) -> float:
    """Convert ruler units without rounding subpixel positions."""
    if not isfinite(value) or not isfinite(dpi) or dpi <= 0:
        raise ValueError("Invalid position or DPI")
    if unit == "px":
        return value
    if unit == "mm":
        return value * dpi / 25.4
    if unit == "inch":
        return value * dpi
    raise ValueError("Invalid unit")


def numeric_guide(
    orientation: GuideAxis,
    position: float,
    *,
    unit: str,
    dpi: tuple[float, float],
    size: tuple[float, float],
    origin: tuple[float, float] = (0.0, 0.0),
) -> GuidePosition:
    """Validate one position measured from the page's ruler origin."""
    if orientation not in ("horizontal", "vertical") or position < 0:
        raise ValueError("Invalid guide")
    axis = 0 if orientation == "vertical" else 1
    value = unit_to_pixels(position, unit, dpi[axis]) + origin[axis]
    if not isfinite(value) or not isfinite(size[axis]) or not 0 <= value <= size[axis]:
        raise ValueError("Guide outside canvas")
    return orientation, value


def batch_guides(
    size: tuple[float, float],
    *,
    columns: int = 1,
    rows: int = 1,
    margins: tuple[float, float, float, float] | None = None,
    center_vertical: bool = False,
    center_horizontal: bool = False,
) -> list[GuidePosition]:
    """Return interior division lines and optional left/top/right/bottom margins."""
    if any(not isfinite(v) or v <= 0 for v in size):
        raise ValueError("Invalid canvas size")
    if any(type(n) is not int or not 1 <= n <= MAX_DIVISIONS for n in (columns, rows)):
        raise ValueError("Invalid division count")
    width, height = size
    result: list[GuidePosition] = []
    left, top, right, bottom = margins if margins is not None else (0.0, 0.0, 0.0, 0.0)
    if any(not isfinite(v) or v < 0 for v in (left, top, right, bottom)):
        raise ValueError("Invalid margins")
    if left + right >= width or top + bottom >= height:
        raise ValueError("Margins leave no content")
    for axis, count, start, end in (
        ("vertical", columns, left, width - right),
        ("horizontal", rows, top, height - bottom),
    ):
        orientation: GuideAxis = "vertical" if axis == "vertical" else "horizontal"
        result.extend((orientation, start + (end - start) * i / count) for i in range(1, count))
        if margins is not None:
            result.extend(((orientation, start), (orientation, end)))
    if center_vertical:
        result.append(("vertical", width / 2))
    if center_horizontal:
        result.append(("horizontal", height / 2))
    return list(dict.fromkeys(result))


def modular_guides(
    size: tuple[float, float],
    *,
    columns: int = 1,
    rows: int = 1,
    margins: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
    column_gutter: float = 0.0,
    row_gutter: float = 0.0,
) -> list[GuidePosition]:
    """Generate column/row edges with unrounded widths and separate gutters."""
    batch_guides(size, columns=columns, rows=rows, margins=margins)
    if any(not isfinite(g) or g < 0 for g in (column_gutter, row_gutter)):
        raise ValueError("Invalid gutter")
    left, top, right, bottom = margins
    result: list[GuidePosition] = []
    for orientation, extent, start, end_margin, count, gutter in (
        ("vertical", size[0], left, right, columns, column_gutter),
        ("horizontal", size[1], top, bottom, rows, row_gutter),
    ):
        width = (extent - start - end_margin - gutter * (count - 1)) / count
        if width <= 0:
            raise ValueError("Gutters leave no content")
        axis: GuideAxis = "vertical" if orientation == "vertical" else "horizontal"
        for index in range(count):
            offset = start + index * (width + gutter)
            result.append((axis, offset))
            # Anchor the final edge to avoid floating-point overflow outside the canvas.
            edge = extent - end_margin if index == count - 1 else offset + width
            result.append((axis, edge))
    return list(dict.fromkeys(result))
