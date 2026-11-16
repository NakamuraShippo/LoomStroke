"""Arc-length placement of layer copies on a vector contour."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF
from PySide6.QtGui import QTransform

from .drawing_assist.object_direction import object_direction
from .drawing_assist.symmetry_transforms import symmetry_transforms
from .path_text.geometry import PathTrack, _sample


def contour_track(
    rect: tuple[float, float, float, float],
    path: dict,
    *,
    start: float = 0.0,
    end: float = 1.0,
    offset: float = 0.0,
    reverse: bool = False,
    rotate: bool = True,
) -> PathTrack | None:
    """Use the same Bezier sampling and distance coordinates as path text."""
    x, y, width, height = rect
    if (
        width <= 0
        or height <= 0
        or not all(math.isfinite(v) for v in (x, y, width, height, start, end, offset))
        or not 0 <= start <= 1
        or not 0 <= end <= 1
    ):
        return None
    anchors = tuple(
        (
            tuple(anchor["pos"]),
            tuple(anchor["h_in"]) if anchor.get("h_in") is not None else None,
            tuple(anchor["h_out"]) if anchor.get("h_out") is not None else None,
        )
        for anchor in path["anchors"]
    )
    closed = bool(path.get("closed"))
    samples = _sample(anchors, closed, width, height)
    if samples is None:
        return None
    walk_start, walk_end = (end, start) if reverse and not closed else (start, end)
    settings = {
        "path": path,
        "start": walk_start,
        "end": walk_end,
        "offset": offset,
        "reverse": reverse,
        "rotate": rotate,
        "full_loop": closed and abs(end - start) >= 1 - 1e-6,
    }
    return PathTrack(settings, samples, (x, y))


def placement_frames(track: PathTrack, count: int, width: float, gap: float) -> list[QTransform]:
    """Return one tangent frame per copy; reject copies outside the interval."""
    if not 1 <= count <= 256 or not math.isfinite(width) or width <= 0:
        raise ValueError("Invalid object count or width")
    if not math.isfinite(gap) or width + gap <= 0:
        raise ValueError("Invalid object gap")
    span = (count - 1) * (width + gap)
    if track.interval_length <= 1e-6 or span > track.interval_length + 1e-6:
        raise ValueError("Objects exceed the selected path interval")
    start_distance = (track.interval_length - span) / 2
    return [track.frame_at(start_distance + index * (width + gap)) for index in range(count)]


def assisted_placement_frames(frames, session):
    """Apply the active ruler to object orientation or repeat the full placement."""
    if session is None:
        return list(frames)
    if session.has_copies:
        return [
            frame * QTransform(m.a, m.c, m.b, m.d, m.tx, m.ty)
            for m in symmetry_transforms(session)
            for frame in frames
        ]
    if session.constraint is None:
        return list(frames)
    result = []
    for frame in frames:
        anchor = frame.map(QPointF())
        tip = frame.map(QPointF(1, 0))
        session.reset()
        session.constrain(anchor.x(), anchor.y())
        session.constrain(tip.x(), tip.y())
        direction = object_direction(session)
        current = math.degrees(math.atan2(tip.y() - anchor.y(), tip.x() - anchor.x()))
        delta = (direction - current + 90) % 180 - 90
        align = QTransform()
        align.translate(anchor.x(), anchor.y())
        align.rotate(delta)
        align.translate(-anchor.x(), -anchor.y())
        result.append(frame * align)
    session.reset()
    return result
