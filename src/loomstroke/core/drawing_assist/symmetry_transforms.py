"""Finite affine copies of a compiled assist, without Qt or tool dependencies."""

from loomstroke.core.geometry import Affine2D

from .stroke_constraint_session import StrokeConstraintSession


def symmetry_transforms(session: StrokeConstraintSession | None) -> tuple[Affine2D, ...]:
    if session is None or not session.has_copies:
        return (Affine2D.identity(),)
    origin = session.copies(0.0, 0.0)
    horizontal = session.copies(1.0, 0.0)
    vertical = session.copies(0.0, 1.0)
    copies = tuple(
        Affine2D(x[0] - o[0], y[0] - o[0], x[1] - o[1], y[1] - o[1], o[0], o[1])
        for o, x, y in zip(origin, horizontal, vertical, strict=True)
    )
    return ((Affine2D.identity(),) if session.include_original else ()) + copies
