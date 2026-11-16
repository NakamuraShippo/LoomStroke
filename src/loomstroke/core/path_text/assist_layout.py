"""Render one editable path text body through its saved drawing assist."""

import math
from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QTransform

from ..drawing_assist import (
    DrawingAssist,
    DrawingAssistValidator,
    PerspectiveConstraint,
    StrokeConstraintSession,
)
from ..drawing_assist.object_direction import object_direction
from ..drawing_assist.symmetry_transforms import symmetry_transforms
from .geometry import PathTrack
from .metadata import path_settings

ASSIST_KEY = "drawing_assist"


def _session(payload):
    settings = path_settings(payload)
    if settings is None or ASSIST_KEY not in settings:
        return None
    assist = DrawingAssist.from_dict(settings[ASSIST_KEY])
    if assist is None or DrawingAssistValidator().validate(assist, (1e7, 1e7)):
        return None
    return StrokeConstraintSession.compile(assist)


def align_path_rotation(payload, rotation):
    session = _session(payload)
    if session is None or session.constraint is None:
        return rotation
    track = PathTrack.from_payload(payload)
    if track is None or track.interval_length <= 1e-6:
        return rotation
    start, tangent = track.at_fraction(track.start)
    tangent = math.radians(tangent + (180 if track.direction < 0 else 0))
    anchor = rotation.map(start)
    end = rotation.map(start + QPointF(math.cos(tangent), math.sin(tangent)))
    session.constrain(anchor.x(), anchor.y())
    session.constrain(end.x(), end.y())
    constraint = session.constraint
    if isinstance(constraint, PerspectiveConstraint):
        valid = {"auto", "horizontal", "vertical"}
        valid.update(f"vp{i + 1}" for i in range(len(constraint.vanishing_points)))
        if constraint.axis not in valid:
            return rotation
    direction = object_direction(session)
    current = math.degrees(math.atan2(end.y() - anchor.y(), end.x() - anchor.x()))
    delta = (direction - current + 90) % 180 - 90
    align = QTransform()
    align.translate(anchor.x(), anchor.y())
    align.rotate(delta)
    align.translate(-anchor.x(), -anchor.y())
    return rotation * align


def path_symmetry_transforms(payload):
    """Canvas-space transformations, including the original when requested."""
    session = _session(payload)
    if session is None or not session.has_copies:
        return ()
    return tuple(QTransform(m.a, m.c, m.b, m.d, m.tx, m.ty) for m in symmetry_transforms(session))


def repeat_path_layout(result, payload, rotation):
    transforms = path_symmetry_transforms(payload)
    if not transforms:
        return result
    inverse, _ = rotation.inverted()
    glyphs = []
    ink, logical = QRectF(), QRectF()
    for world in transforms:
        local = rotation * world * inverse
        ink = ink.united(local.mapRect(result.ink_bounds))
        logical = logical.united(local.mapRect(result.logical_bounds))
        glyphs.extend(
            replace(
                glyph,
                transform=glyph.transform * local,
                cell=local.mapRect(glyph.cell),
                group_cell=(
                    local.mapRect(glyph.group_cell) if glyph.group_cell is not None else None
                ),
            )
            for glyph in result.glyphs
        )
    return replace(result, glyphs=tuple(glyphs), ink_bounds=ink, logical_bounds=logical)
