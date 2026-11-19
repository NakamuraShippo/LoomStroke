"""Handle geometry for the raster selection's four-corner transform modes."""

from __future__ import annotations

from loomstroke.core.selection_projective import Quad, valid_quad

_CORNERS = {"corner-tl": 0, "corner-tr": 1, "corner-br": 2, "corner-bl": 3}
_EDGES = {
    "edge-top": (0, 1),
    "edge-right": (1, 2),
    "edge-bottom": (2, 3),
    "edge-left": (3, 0),
}


def quad_handles(quad: Quad) -> dict[str, tuple[float, float]]:
    handles = {name: quad[index] for name, index in _CORNERS.items()}
    for name, (first, second) in _EDGES.items():
        handles[name] = (
            (quad[first][0] + quad[second][0]) * 0.5,
            (quad[first][1] + quad[second][1]) * 0.5,
        )
    return handles


def drag_quad(  # noqa: PLR0912
    quad: Quad,
    handle: str,
    mode: str,
    dx: float,
    dy: float,
    *,
    shift_constrain: bool = False,
) -> Quad | None:
    """Move one or more corners while preserving a usable quadrilateral."""
    points = [list(point) for point in quad]
    if handle == "move-area":
        for point in points:
            point[0] += dx
            point[1] += dy
    elif handle in _EDGES:
        if mode == "skew":
            if handle in {"edge-top", "edge-bottom"}:
                dy = 0.0
            else:
                dx = 0.0
        for index in _EDGES[handle]:
            points[index][0] += dx
            points[index][1] += dy
    elif handle in _CORNERS:
        index = _CORNERS[handle]
        if shift_constrain or mode == "distort":
            if abs(dx) >= abs(dy):
                dy = 0.0
            else:
                dx = 0.0
        if mode == "skew":
            neighbor = (index + (1 if index in (0, 2) else -1)) % 4
            for target in (index, neighbor):
                points[target][0] += dx
                points[target][1] += dy
        elif mode == "perspective":
            row_peer = index ^ 1 if index < 2 else 5 - index
            column_peer = 3 if index == 0 else 2 if index == 1 else 1 if index == 2 else 0
            points[index][0] += dx
            points[row_peer][0] -= dx
            points[index][1] += dy
            points[column_peer][1] -= dy
        else:
            points[index][0] += dx
            points[index][1] += dy
    else:
        return None
    result: Quad = tuple((float(x), float(y)) for x, y in points)  # type: ignore[assignment]
    return result if valid_quad(result) else None


def point_inside_quad(quad: Quad, x: float, y: float) -> bool:
    signs = []
    for index, start in enumerate(quad):
        end = quad[(index + 1) % 4]
        signs.append((end[0] - start[0]) * (y - start[1]) - (end[1] - start[1]) * (x - start[0]))
    return all(value >= 0.0 for value in signs) or all(value <= 0.0 for value in signs)
