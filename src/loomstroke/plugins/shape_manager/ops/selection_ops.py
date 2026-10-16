from __future__ import annotations

import logging
from collections.abc import Iterable

from ...vector_tools.common import Anchor, VectorObject, VectorPath, VectorStyle

logger = logging.getLogger(__name__)


def translate_object(obj: VectorObject, dx: float, dy: float) -> VectorObject:
    new_paths: list[VectorPath] = []
    for path in obj.paths:
        anchors: list[Anchor] = []
        for anchor in path.anchors:
            pos = (anchor.pos[0] + dx, anchor.pos[1] + dy)
            anchors.append(
                Anchor(
                    pos=pos,
                    kind=anchor.kind,
                    h_in=anchor.h_in,
                    h_out=anchor.h_out,
                )
            )
        new_paths.append(VectorPath(anchors=anchors, closed=path.closed))
    meta = _translate_parametric_bbox(dict(obj.meta), dx, dy)
    return VectorObject(
        id=obj.id,
        paths=new_paths,
        style=obj.style,
        meta=meta,
        preview=obj.preview,
    )


def scale_object(
    obj: VectorObject,
    pivot: tuple[float, float],
    sx: float,
    sy: float,
    *,
    keep_stroke_width: bool,
) -> VectorObject:
    px, py = pivot
    new_paths: list[VectorPath] = []
    for path in obj.paths:
        anchors: list[Anchor] = []
        for anchor in path.anchors:
            pos = anchor.pos
            new_pos = (px + (pos[0] - px) * sx, py + (pos[1] - py) * sy)
            h_in = _scale_handle(anchor.h_in, pos, new_pos, pivot, sx, sy)
            h_out = _scale_handle(anchor.h_out, pos, new_pos, pivot, sx, sy)
            kind = "smooth" if (h_in or h_out) else anchor.kind
            anchors.append(Anchor(pos=new_pos, kind=kind, h_in=h_in, h_out=h_out))
        new_paths.append(VectorPath(anchors=anchors, closed=path.closed))

    style = obj.style
    if not keep_stroke_width:
        factor = (abs(sx) + abs(sy)) * 0.5
        if factor <= 0:
            factor = 1.0
        style = VectorStyle(
            stroke_color=style.stroke_color,
            stroke_width=style.stroke_width * factor,
            fill_enabled=style.fill_enabled,
            fill_color=style.fill_color,
            join=style.join,
            cap=style.cap,
            miter_limit=style.miter_limit,
            dash=style.dash,
        )

    meta = _scale_parametric_bbox(dict(obj.meta), pivot, sx, sy)
    return VectorObject(
        id=obj.id,
        paths=new_paths,
        style=style,
        meta=meta,
        preview=obj.preview,
    )


def _translate_parametric_bbox(meta: dict[str, object], dx: float, dy: float) -> dict[str, object]:
    """Update parametric reference_bbox for translation."""
    raw = meta.get("shape_manager_parametric")
    if not isinstance(raw, dict):
        return meta
    payload = dict(raw)
    ref = payload.get("reference_bbox")
    if not isinstance(ref, (list, tuple)) or len(ref) != 4:
        return meta
    try:
        payload["reference_bbox"] = [
            float(ref[0]) + dx,
            float(ref[1]) + dy,
            float(ref[2]) + dx,
            float(ref[3]) + dy,
        ]
        meta["shape_manager_parametric"] = payload
    except (TypeError, ValueError):
        logger.debug("parametric bbox translate failed", exc_info=True)
    return meta


def _scale_parametric_bbox(
    meta: dict[str, object],
    pivot: tuple[float, float],
    sx: float,
    sy: float,
) -> dict[str, object]:
    """Update parametric reference_bbox for scaling."""
    raw = meta.get("shape_manager_parametric")
    if not isinstance(raw, dict):
        return meta
    payload = dict(raw)
    ref = payload.get("reference_bbox")
    if not isinstance(ref, (list, tuple)) or len(ref) != 4:
        return meta
    try:
        px, py = pivot
        payload["reference_bbox"] = [
            px + (float(ref[0]) - px) * sx,
            py + (float(ref[1]) - py) * sy,
            px + (float(ref[2]) - px) * sx,
            py + (float(ref[3]) - py) * sy,
        ]
        meta["shape_manager_parametric"] = payload
    except (TypeError, ValueError):
        logger.debug("parametric bbox scale failed", exc_info=True)
    return meta


def _scale_handle(
    handle: tuple[float, float] | None,
    original_pos: tuple[float, float],
    new_pos: tuple[float, float],
    pivot: tuple[float, float],
    sx: float,
    sy: float,
) -> tuple[float, float] | None:
    if handle is None:
        return None
    abs_pos = (original_pos[0] + handle[0], original_pos[1] + handle[1])
    px, py = pivot
    abs_scaled = (px + (abs_pos[0] - px) * sx, py + (abs_pos[1] - py) * sy)
    return (abs_scaled[0] - new_pos[0], abs_scaled[1] - new_pos[1])


def rotate_object(
    obj: VectorObject,
    pivot: tuple[float, float],
    angle: float,
) -> VectorObject:
    """Rotate a VectorObject around *pivot* by *angle* radians."""
    import math  # noqa: PLC0415

    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    px, py = pivot

    new_paths: list[VectorPath] = []
    for path in obj.paths:
        anchors: list[Anchor] = []
        for anchor in path.anchors:
            rx, ry = anchor.pos[0] - px, anchor.pos[1] - py
            new_pos = (px + rx * cos_a - ry * sin_a, py + rx * sin_a + ry * cos_a)
            h_in = _rotate_handle(anchor.h_in, cos_a, sin_a)
            h_out = _rotate_handle(anchor.h_out, cos_a, sin_a)
            anchors.append(Anchor(pos=new_pos, kind=anchor.kind, h_in=h_in, h_out=h_out))
        new_paths.append(VectorPath(anchors=anchors, closed=path.closed))

    meta = _rotate_parametric_bbox(dict(obj.meta), pivot, cos_a, sin_a)
    return VectorObject(
        id=obj.id,
        paths=new_paths,
        style=obj.style,
        meta=meta,
        preview=obj.preview,
    )


def _rotate_handle(
    handle: tuple[float, float] | None, cos_a: float, sin_a: float
) -> tuple[float, float] | None:
    if handle is None:
        return None
    hx, hy = handle
    return (hx * cos_a - hy * sin_a, hx * sin_a + hy * cos_a)


def _rotate_parametric_bbox(
    meta: dict[str, object],
    pivot: tuple[float, float],
    cos_a: float,
    sin_a: float,
) -> dict[str, object]:
    """Update parametric reference_bbox for rotation (best-effort AABB)."""
    raw = meta.get("shape_manager_parametric")
    if not isinstance(raw, dict):
        return meta
    payload = dict(raw)
    ref = payload.get("reference_bbox")
    if not isinstance(ref, (list, tuple)) or len(ref) != 4:
        return meta
    try:
        px, py = pivot
        corners = [
            (float(ref[0]), float(ref[1])),
            (float(ref[2]), float(ref[1])),
            (float(ref[2]), float(ref[3])),
            (float(ref[0]), float(ref[3])),
        ]
        rotated = []
        for cx, cy in corners:
            rx, ry = cx - px, cy - py
            rotated.append((px + rx * cos_a - ry * sin_a, py + rx * sin_a + ry * cos_a))
        xs = [p[0] for p in rotated]
        ys = [p[1] for p in rotated]
        payload["reference_bbox"] = [min(xs), min(ys), max(xs), max(ys)]
        meta["shape_manager_parametric"] = payload
    except (TypeError, ValueError):
        logger.debug("parametric bbox rotate failed", exc_info=True)
    return meta


def apply_transforms(
    objects: Iterable[VectorObject],
    *,
    translate: tuple[float, float] | None = None,
    scale: tuple[float, float, float, float] | None = None,
    keep_stroke_width: bool = True,
) -> list[VectorObject]:
    updated: list[VectorObject] = []
    for obj in objects:
        current = obj
        if scale is not None:
            px, py, sx, sy = scale
            current = scale_object(current, (px, py), sx, sy, keep_stroke_width=keep_stroke_width)
        if translate is not None:
            dx, dy = translate
            current = translate_object(current, dx, dy)
        updated.append(current)
    return updated
