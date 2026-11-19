"""Draggable controls for a raster selection mesh."""

from __future__ import annotations

from loomstroke.core.selection_mesh import Mesh, valid_mesh


def mesh_handles(mesh: Mesh) -> dict[str, tuple[float, float]]:
    return {f"mesh-{index}": point for index, point in enumerate(mesh)}


def drag_mesh(mesh: Mesh, handle: str, dx: float, dy: float) -> Mesh | None:
    if handle == "move-area":
        points = tuple((x + dx, y + dy) for x, y in mesh)
    elif handle.startswith("mesh-") and handle[5:].isdigit():
        index = int(handle[5:])
        if index >= len(mesh):
            return None
        points = tuple(
            (x + dx, y + dy) if current == index else (x, y) for current, (x, y) in enumerate(mesh)
        )
    else:
        return None
    return points if valid_mesh(points) else None
