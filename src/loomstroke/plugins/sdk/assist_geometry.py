"""Shared geometry helpers for non-brush tools using existing assist snapshots."""

from dataclasses import replace

from PySide6.QtGui import QPainterPath, QTransform

from loomstroke.core.drawing_assist.symmetry_transforms import symmetry_transforms

from .drawing_assist import get_drawing_assist_controller


def snap_assist_point(host, point):
    position = snap_assist_coordinates(host, point.pos)
    return replace(point, pos=position) if position != point.pos else point


def snap_assist_coordinates(host, position):
    controller = get_drawing_assist_controller(host)
    snap = getattr(controller, "snap_position", None)
    if not callable(snap):
        return position
    x, y, geometry = snap(*position)
    controller.set_position_feedback(geometry)
    return x, y


def mapped_paths(path: QPainterPath, session):
    transforms = symmetry_transforms(session)
    if (
        len(transforms) == 1
        and transforms[0].a == transforms[0].d == 1
        and not (transforms[0].b or transforms[0].c or transforms[0].tx or transforms[0].ty)
    ):
        return (path,)
    return tuple(
        QTransform(matrix.a, matrix.c, matrix.b, matrix.d, matrix.tx, matrix.ty).map(path)
        for matrix in transforms
    )


def combine_paths(paths, *, union=True):
    result = QPainterPath()
    for mapped in paths:
        if union:
            result = mapped if result.isEmpty() else result.united(mapped)
        else:
            result.addPath(mapped)
    return result


def path_copies(path: QPainterPath, session, *, union=True) -> QPainterPath:
    return combine_paths(mapped_paths(path, session), union=union)


def seed_copies(point, session, size):
    width, height = size
    return tuple(
        dict.fromkeys(
            (round(x), round(y))
            for matrix in symmetry_transforms(session)
            for x, y in (matrix.apply(*point),)
            if 0 <= round(x) < width and 0 <= round(y) < height
        )
    )
