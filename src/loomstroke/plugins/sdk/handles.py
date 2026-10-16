"""Shared transform handle geometry helpers for tool plugins."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

HANDLE_CORNER_SIZE = 10.0
HANDLE_EDGE_SIZE = 10.0
HANDLE_DETECT_PAD = 6.0
HANDLE_ROTATION_OFFSET = 12.0

CORNER_HANDLE_ORDER = ("corner-tl", "corner-tr", "corner-bl", "corner-br")
EDGE_HANDLE_ORDER = ("edge-top", "edge-bottom", "edge-left", "edge-right")


def point_in_radius(px: float, py: float, cx: float, cy: float, r: float) -> bool:
    """Return True if (px, py) is within *r* of (cx, cy)."""
    dx = px - cx
    dy = py - cy
    return (dx * dx + dy * dy) <= r * r


def handle_positions_for_rect(
    rect: tuple[float, float, float, float],
    *,
    include_rotate: bool = False,
) -> dict[str, tuple[float, float]]:
    """Return local transform handle positions for ``(x, y, width, height)``."""
    x, y, width, height = rect
    left = float(x)
    top = float(y)
    right = left + float(width)
    bottom = top + float(height)
    cx = left + float(width) * 0.5
    cy = top + float(height) * 0.5
    positions = {
        "corner-tl": (left, top),
        "corner-tr": (right, top),
        "corner-br": (right, bottom),
        "corner-bl": (left, bottom),
        "edge-top": (cx, top),
        "edge-right": (right, cy),
        "edge-bottom": (cx, bottom),
        "edge-left": (left, cy),
    }
    if include_rotate:
        positions["rotate"] = (cx, top - HANDLE_ROTATION_OFFSET)
    return positions


def handle_positions_for_bounds(
    bounds: tuple[float, float, float, float],
    *,
    include_rotate: bool = True,
) -> dict[str, tuple[float, float]]:
    """Return transform handle positions for ``(min_x, min_y, max_x, max_y)``."""
    min_x, min_y, max_x, max_y = bounds
    return handle_positions_for_rect(
        (min_x, min_y, max_x - min_x, max_y - min_y),
        include_rotate=include_rotate,
    )


def handle_anchor_for_rect(
    handle: str,
    rect: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Return the opposite anchor point for *handle* in local rect coordinates."""
    x, y, width, height = rect
    positions = handle_positions_for_rect(rect)
    opposite = {
        "corner-tl": "corner-br",
        "corner-tr": "corner-bl",
        "corner-bl": "corner-tr",
        "corner-br": "corner-tl",
        "edge-top": "edge-bottom",
        "edge-bottom": "edge-top",
        "edge-left": "edge-right",
        "edge-right": "edge-left",
    }.get(handle)
    if opposite is not None:
        return positions[opposite]
    return (float(x) + float(width) * 0.5, float(y) + float(height) * 0.5)


def handle_position_for_rect(
    handle: str,
    rect: tuple[float, float, float, float],
    *,
    include_rotate: bool = False,
) -> tuple[float, float] | None:
    """Return a single local handle position, or ``None`` for unsupported handles."""
    return handle_positions_for_rect(rect, include_rotate=include_rotate).get(handle)


def hit_test_handle_points(
    px: float,
    py: float,
    handles: Mapping[str, tuple[float, float]],
    *,
    include_move_area: bool = False,
    contains_move_area: Callable[[float, float], bool] | None = None,
    corner_half_extent: float | None = None,
    edge_radius: float | None = None,
    rotation_radius: float | None = None,
    corner_inclusive: bool = True,
    rotation_first: bool = True,
    corner_order: Sequence[str] = CORNER_HANDLE_ORDER,
    edge_order: Sequence[str] = EDGE_HANDLE_ORDER,
) -> tuple[str, str] | None:
    """Hit-test precomputed transform handle points."""
    half_corner = (
        HANDLE_CORNER_SIZE * 0.5 + HANDLE_DETECT_PAD
        if corner_half_extent is None
        else corner_half_extent
    )
    half_edge = HANDLE_EDGE_SIZE * 0.5 + HANDLE_DETECT_PAD if edge_radius is None else edge_radius
    rot_radius = half_edge if rotation_radius is None else rotation_radius

    def _hit_rotate() -> tuple[str, str] | None:
        position = handles.get("rotate")
        if position is None:
            return None
        hx, hy = position
        if point_in_radius(px, py, hx, hy, rot_radius):
            return ("rotate", "rotate")
        return None

    def _hit_corner(hx: float, hy: float) -> bool:
        if corner_inclusive:
            return abs(px - hx) <= half_corner and abs(py - hy) <= half_corner
        return abs(px - hx) < half_corner and abs(py - hy) < half_corner

    if rotation_first:
        rotated = _hit_rotate()
        if rotated is not None:
            return rotated

    for name in corner_order:
        position = handles.get(name)
        if position is None:
            continue
        hx, hy = position
        if _hit_corner(hx, hy):
            return (name, "scale")

    if not rotation_first:
        rotated = _hit_rotate()
        if rotated is not None:
            return rotated

    for name in edge_order:
        position = handles.get(name)
        if position is None:
            continue
        hx, hy = position
        if point_in_radius(px, py, hx, hy, half_edge):
            return (name, "scale")

    if include_move_area and contains_move_area is not None and contains_move_area(px, py):
        return ("move-area", "move")
    return None
