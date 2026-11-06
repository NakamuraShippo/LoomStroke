"""Pure geometry edits and hit testing for existing perspective assists."""

from .drawing_assist import DrawingAssist, Point


def with_point_count(assist: DrawingAssist, count: int, size: Point) -> DrawingAssist:
    if assist.type != "perspective" or count not in (1, 2, 3):
        raise ValueError("perspective requires 1 to 3 vanishing points")
    width, height = size
    points = list(assist.points("vanishing_points")[:count])
    defaults = (
        (width * 0.5, height * 0.5),
        (width * 0.9, height * 0.5),
        (width * 0.5, height * 0.1),
    )
    points.extend(defaults[len(points) : count])
    updated = assist.with_geometry(vanishing_points=[list(point) for point in points])
    axis = str(assist.option("axis", "auto"))
    if axis.startswith("vp") and axis not in {f"vp{i + 1}" for i in range(count)}:
        updated = updated.with_options(axis="auto")
    return updated


def with_vanishing_point(assist: DrawingAssist, index: int, point: Point) -> DrawingAssist:
    points = list(assist.points("vanishing_points"))
    if assist.type != "perspective" or not 0 <= index < len(points):
        raise ValueError("invalid vanishing point")
    points[index] = point
    return assist.with_geometry(vanishing_points=[list(value) for value in points])


def nearest_vanishing_point(assist: DrawingAssist, point: Point, radius: float) -> int | None:
    hits = (
        (sum((a - b) ** 2 for a, b in zip(vp, point, strict=True)), index)
        for index, vp in enumerate(assist.points("vanishing_points"))
    )
    closest = min(hits, default=(float("inf"), -1))
    return closest[1] if closest[0] <= radius**2 else None
