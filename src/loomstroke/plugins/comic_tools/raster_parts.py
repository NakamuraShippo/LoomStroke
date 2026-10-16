"""Raster Preserve parts: the drawn pixels are the balloon's visual authority.

A raster-preserve balloon keeps the cropped source RGBA of its body and its
tail (PNG base64 on the BalloonRecord) and displays them on ONE dedicated
raster "part layer" per balloon. Moving body or tail only changes the
placement rects and re-blits the untouched crops; scaling resamples at
display time while the stored crops stay pristine. Body and tail are never
flattened into each other or unioned — a detached tail simply blits at its
own rect.
"""

from __future__ import annotations

import base64
import functools
import itertools
import math
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QBuffer, QIODevice, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QTransform

from .model import (
    BODY_GEOMETRY_RASTER,
    TAIL_GEOMETRY_RASTER,
    BalloonRecord,
    denormalize_outline,
    is_raster_rig,
)

#: layer-metadata marker of a balloon's raster part layer
RASTER_PART_NAMESPACE = "loomstroke.comictools.balloon_raster"


def encode_qimage_png(image: QImage) -> str:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return base64.b64encode(bytes(buffer.data())).decode("ascii")


def decode_png_qimage(data: str) -> QImage | None:
    if not data:
        return None
    image = QImage()
    if not image.loadFromData(base64.b64decode(data.encode("ascii")), "PNG"):
        return None
    return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def tight_alpha_crop(image: QImage) -> tuple[QImage, tuple[float, float, float, float]] | None:
    """Crop a layer image to its non-transparent bbox (the drawn pixels)."""

    from loomstroke.core.document_snapshot_builder import (  # noqa: PLC0415
        DocumentSnapshotBuilder,
    )

    rgba = DocumentSnapshotBuilder.qimage_to_array(image)
    import numpy as np  # noqa: PLC0415

    alpha = np.asarray(rgba)[:, :, 3]
    ys, xs = np.nonzero(alpha > 8)
    if xs.size == 0:
        return None
    min_x, max_x = int(xs.min()), int(xs.max())
    min_y, max_y = int(ys.min()), int(ys.max())
    crop = image.convertToFormat(QImage.Format.Format_ARGB32).copy(
        min_x, min_y, max_x - min_x + 1, max_y - min_y + 1
    )
    bbox = (float(min_x), float(min_y), float(max_x - min_x + 1), float(max_y - min_y + 1))
    return crop, bbox


def part_layer_marker(balloon_id: str) -> dict[str, Any]:
    return {"plugins": {RASTER_PART_NAMESPACE: {"schema_version": 1, "balloon_id": balloon_id}}}


def part_layer_balloon_id(layer: Any) -> str | None:
    metadata = getattr(layer, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    plugins = metadata.get("plugins")
    if not isinstance(plugins, Mapping):
        return None
    entry = plugins.get(RASTER_PART_NAMESPACE)
    if not isinstance(entry, Mapping):
        return None
    balloon_id = str(entry.get("balloon_id") or "")
    return balloon_id or None


def find_part_layer(canvas: Any, balloon_id: str) -> Any | None:
    stack = list(getattr(canvas, "_layers", None) or [])
    while stack:
        layer = stack.pop()
        stack.extend(getattr(layer, "children", None) or [])
        if part_layer_balloon_id(layer) == balloon_id:
            return layer
    return None


def _blit(painter: QPainter, crop: QImage, rect: tuple[float, float, float, float]) -> None:
    x, y, w, h = rect
    same_size = abs(w - crop.width()) < 0.5 and abs(h - crop.height()) < 0.5
    if same_size:
        # pixel-perfect placement: a pure move never resamples the crop
        painter.drawImage(QPoint(round(x), round(y)), crop)
        return
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.drawImage(QRectF(x, y, w, h), crop)


def tail_root_on_body(record: BalloonRecord, tip: tuple[float, float]) -> tuple[float, float]:
    """The tail's base point ON the body boundary toward ``tip``.

    Uses the helper body outline when present (the hand-drawn silhouette),
    else the body_rect ellipse — same idea as the parametric speech shapes:
    the root always sits on the balloon edge along center→tip, so body
    moves/resizes and tip drags keep the connection attached.
    """

    x, y, w, h = record.body_rect
    cx, cy = x + w * 0.5, y + h * 0.5
    dx, dy = tip[0] - cx, tip[1] - cy
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return (cx, cy + h * 0.5)
    ux, uy = dx / length, dy / length
    if len(record.body_outline) >= 3:
        points = denormalize_outline(record.body_outline, record.body_rect)
        best_t = None
        count = len(points)
        for index in range(count):
            ax, ay = points[index]
            bx, by = points[(index + 1) % count]
            # segment (a->b) vs ray (c + t*u)
            ex, ey = bx - ax, by - ay
            denom = ux * ey - uy * ex
            if abs(denom) < 1e-9:
                continue
            t_ray = ((ax - cx) * ey - (ay - cy) * ex) / denom
            if t_ray <= 0:
                continue
            if abs(ex) >= abs(ey):
                s = (cx + t_ray * ux - ax) / ex if abs(ex) > 1e-9 else -1.0
            else:
                s = (cy + t_ray * uy - ay) / ey if abs(ey) > 1e-9 else -1.0
            if 0.0 <= s <= 1.0 and (best_t is None or t_ray < best_t):
                best_t = t_ray
        if best_t is not None:
            return (cx + best_t * ux, cy + best_t * uy)
    # ellipse fallback (matches the round template balloon)
    rx, ry = max(1.0, w * 0.5), max(1.0, h * 0.5)
    scale = 1.0 / math.sqrt((ux / rx) ** 2 + (uy / ry) ** 2)
    return (cx + ux * scale, cy + uy * scale)


def _axis_stretch_transform(
    src_a: tuple[float, float],
    src_b: tuple[float, float],
    dst_a: tuple[float, float],
    dst_b: tuple[float, float],
) -> QTransform | None:
    """Map a->a', b->b' stretching ONLY along the a-b axis.

    The perpendicular (width) scale stays 1.0, so pulling a tail longer
    keeps the imported pixel ratio of its line width instead of scaling
    the whole crop up.
    """

    sx, sy = src_b[0] - src_a[0], src_b[1] - src_a[1]
    dx, dy = dst_b[0] - dst_a[0], dst_b[1] - dst_a[1]
    src_len = math.hypot(sx, sy)
    dst_len = math.hypot(dx, dy)
    if src_len < 1e-6 or dst_len < 1e-6:
        return None
    src_angle = math.degrees(math.atan2(sy, sx))
    dst_angle = math.degrees(math.atan2(dy, dx))
    stretch = dst_len / src_len
    transform = QTransform()
    transform.translate(dst_a[0], dst_a[1])
    transform.rotate(dst_angle)
    transform.scale(stretch, 1.0)
    transform.rotate(-src_angle)
    transform.translate(-src_a[0], -src_a[1])
    return transform


@functools.lru_cache(maxsize=256)
def _outline_geometry(
    outline: tuple[tuple[float, float], ...], rect: tuple[float, float, float, float]
) -> tuple[tuple[tuple[float, float], ...], tuple[float, ...], tuple[float, ...], float]:
    """(points, segment lengths, cumulative lengths, perimeter) of the page
    outline for ``(outline, rect)`` — cached: every anchor lookup used to
    re-walk the polygon."""

    if len(outline) >= 3:
        points = tuple(denormalize_outline(outline, rect))
    else:
        x, y, w, h = rect
        cx, cy = x + w * 0.5, y + h * 0.5
        points = tuple(
            (
                cx + 0.5 * w * math.cos(index / 72 * 2 * math.pi),
                cy + 0.5 * h * math.sin(index / 72 * 2 * math.pi),
            )
            for index in range(72)
        )
    count = len(points)
    lengths = []
    cum = []
    total = 0.0
    for index in range(count):
        ax, ay = points[index]
        bx, by = points[(index + 1) % count]
        cum.append(total)
        seg = math.hypot(bx - ax, by - ay)
        lengths.append(seg)
        total += seg
    return points, tuple(lengths), tuple(cum), total


def _geometry(record: BalloonRecord):
    outline = tuple((float(px), float(py)) for px, py in record.body_outline)
    rect = tuple(float(v) for v in record.body_rect)
    return _outline_geometry(outline, rect)  # type: ignore[arg-type]


def _body_outline_points(record: BalloonRecord) -> list[tuple[float, float]]:
    """Page-space body outline (helper contour scaled to body_rect); ellipse
    fallback matches the round template balloon."""

    return list(_geometry(record)[0])


def outline_perimeter(record: BalloonRecord) -> float:
    """Length (page px) of the body outline."""

    return _geometry(record)[3]


def outline_point_at_param(record: BalloonRecord, param: float) -> tuple[float, float]:
    """Point on the body outline at normalized arc-length ``param`` (0..1)."""

    points, lengths, cum, total = _geometry(record)
    if total <= 0.0:
        return points[0]
    target = (param % 1.0) * total
    count = len(points)
    # binary search on the cumulative table
    lo, hi = 0, count - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if cum[mid] <= target:
            lo = mid
        else:
            hi = mid - 1
    index = lo
    frac = (target - cum[index]) / max(1e-9, lengths[index])
    frac = max(0.0, min(1.0, frac))
    ax, ay = points[index]
    bx, by = points[(index + 1) % count]
    return (ax + (bx - ax) * frac, ay + (by - ay) * frac)


def nearest_outline_param(record: BalloonRecord, point: tuple[float, float]) -> float:
    """Normalized arc-length param of the outline point closest to ``point``."""

    points, lengths, cum, total = _geometry(record)
    count = len(points)
    best = (float("inf"), 0.0)
    px, py = point
    for index in range(count):
        ax, ay = points[index]
        bx, by = points[(index + 1) % count]
        ex, ey = bx - ax, by - ay
        seg_sq = ex * ex + ey * ey
        s = 0.0 if seg_sq < 1e-9 else max(0.0, min(1.0, ((px - ax) * ex + (py - ay) * ey) / seg_sq))
        qx, qy = ax + ex * s, ay + ey * s
        distance = math.hypot(px - qx, py - qy)
        if distance < best[0]:
            best = (distance, (cum[index] + lengths[index] * s) / max(1e-9, total))
    return best[1]


def outline_param_facing(record: BalloonRecord, tip: tuple[float, float]) -> float:
    """Outline param where the ray from the body centre through ``tip``
    leaves the balloon (outermost intersection). Continuous as the tip
    orbits — unlike the nearest point, which jumps across the medial axis.
    Falls back to the nearest param when the ray misses (degenerate)."""

    points, lengths, cum, total = _geometry(record)
    x, y, w, h = record.body_rect
    cx, cy = x + w * 0.5, y + h * 0.5
    dx, dy = tip[0] - cx, tip[1] - cy
    norm = math.hypot(dx, dy)
    if norm < 1e-6 or total <= 0.0:
        return nearest_outline_param(record, tip)
    dx /= norm
    dy /= norm
    best_t = -1.0
    best_param = None
    count = len(points)
    for index in range(count):
        ax, ay = points[index]
        bx, by = points[(index + 1) % count]
        ex, ey = bx - ax, by - ay
        denom = dx * ey - dy * ex
        if abs(denom) < 1e-12:
            continue
        # solve c + d*t = a + e*s
        rx, ry = ax - cx, ay - cy
        tt = (rx * ey - ry * ex) / denom
        ss = (rx * dy - ry * dx) / denom
        if tt <= 0.0 or ss < 0.0 or ss > 1.0:
            continue
        if tt > best_t:
            best_t = tt
            best_param = (cum[index] + lengths[index] * ss) / total
    if best_param is None:
        return nearest_outline_param(record, tip)
    return best_param % 1.0


def ensure_root_authority(record: BalloonRecord) -> BalloonRecord:
    """Records that predate ``tail_root_param`` (legacy ``tail_base_params``
    only): derive the root authority so every consumer reads one truth."""

    if record.tail_root_param is not None or record.tail_base_params is None:
        return record
    legacy = _legacy_root(record)
    if legacy is None:
        return record
    from dataclasses import replace as _replace  # noqa: PLC0415

    return _replace(record, tail_root_param=legacy[0], tail_root_half_px=legacy[1])


def ensure_tail_base_defaults(record: BalloonRecord) -> BalloonRecord:
    """Derive default 2-point base anchors for raster tails that predate
    them (so the green handles always appear). Uses the stored helper tail
    outline; persisted naturally by the next balloon update command."""

    if (
        record.tail_geometry_mode != TAIL_GEOMETRY_RASTER
        or record.tail_base_params is not None
        or record.tail_root_param is not None
        or record.tail_rect is None
        or record.tail_local_root is None
        or record.tail_local_tip is None
        or len(record.tail_outline) < 3
    ):
        return record
    from dataclasses import replace as _replace  # noqa: PLC0415

    tx, ty, tw, th = record.tail_rect
    points = [(px * tw, py * th) for px, py in record.tail_outline]
    root = record.tail_local_root
    tip = record.tail_local_tip
    ax, ay = tip[0] - root[0], tip[1] - root[1]
    length = math.hypot(ax, ay)
    if length < 4.0:
        return record
    ux, uy = ax / length, ay / length
    projections = [(px - root[0]) * ux + (py - root[1]) * uy for px, py in points]
    lo, hi = min(projections), max(projections)
    band_limit = lo + max(3.0, (hi - lo) * 0.22)
    band = [pt for pt, proj in zip(points, projections, strict=True) if proj <= band_limit]
    if len(band) < 2:
        return record
    perp_values = [(px - root[0]) * -uy + (py - root[1]) * ux for px, py in band]
    base_a = band[perp_values.index(min(perp_values))]
    base_b = band[perp_values.index(max(perp_values))]
    if math.hypot(base_b[0] - base_a[0], base_b[1] - base_a[1]) < 3.0:
        return record
    outline_pts = _body_outline_points(record)
    perimeter = sum(
        math.hypot(
            outline_pts[(i + 1) % len(outline_pts)][0] - outline_pts[i][0],
            outline_pts[(i + 1) % len(outline_pts)][1] - outline_pts[i][1],
        )
        for i in range(len(outline_pts))
    )
    page_root = (tx + root[0], ty + root[1])
    center_param = nearest_outline_param(record, page_root)
    base_width = math.hypot(base_b[0] - base_a[0], base_b[1] - base_a[1])
    half = min(0.2, (base_width * 0.5) / max(1.0, perimeter))
    return _replace(
        record,
        tail_base_params=((center_param - half) % 1.0, (center_param + half) % 1.0),
        tail_local_base=(base_a, base_b),
        tail_root_param=center_param,
        tail_root_half_px=max(0.5, base_width * 0.5),
    )


def _affine_from_three_points(src, dst) -> QTransform | None:
    """QTransform mapping three source points onto three destination points."""

    (x0, y0), (x1, y1), (x2, y2) = src
    (u0, v0), (u1, v1), (u2, v2) = dst
    det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(det) < 1e-9:
        return None
    a11 = ((u1 - u0) * (y2 - y0) - (u2 - u0) * (y1 - y0)) / det
    a12 = ((u2 - u0) * (x1 - x0) - (u1 - u0) * (x2 - x0)) / det
    a21 = ((v1 - v0) * (y2 - y0) - (v2 - v0) * (y1 - y0)) / det
    a22 = ((v2 - v0) * (x1 - x0) - (v1 - v0) * (x2 - x0)) / det
    tx = u0 - a11 * x0 - a12 * y0
    ty = v0 - a21 * x0 - a22 * y0
    return QTransform(a11, a21, a12, a22, tx, ty)


def _legacy_root(record: BalloonRecord) -> tuple[float, float] | None:
    """(centre param, half px) derived from legacy ``tail_base_params``."""

    if record.tail_base_params is None:
        return None
    a, b = record.tail_base_params
    spacing = (b - a) % 1.0
    if spacing > 0.5:
        spacing -= 1.0
    spacing = max(-0.45, min(0.45, spacing))
    centre = (a + spacing * 0.5) % 1.0
    return centre, abs(spacing) * 0.5 * outline_perimeter(record)


def tail_root_half_px(record: BalloonRecord) -> float | None:
    """Half span (page px) of the tail root / opening."""

    if record.tail_root_half_px is not None:
        return max(0.5, float(record.tail_root_half_px))
    legacy = _legacy_root(record)
    return None if legacy is None else max(0.5, legacy[1])


def effective_tail_root_param(record: BalloonRecord, tip: tuple[float, float]) -> float | None:
    """Outline param of the tail ROOT centre for ``tip``: the pinned
    ``tail_root_param`` (follow off) or the ray-facing param + manual
    ``tail_base_offset`` (follow on). None for legacy-less records."""

    if record.tail_root_param is not None:
        pinned = float(record.tail_root_param) % 1.0
    else:
        legacy = _legacy_root(record)
        if legacy is None:
            return None
        pinned = legacy[0]
    if not record.tail_follow_tip:
        return pinned
    return (outline_param_facing(record, tip) + float(record.tail_base_offset)) % 1.0


def effective_tail_base_params(
    record: BalloonRecord, tip: tuple[float, float]
) -> tuple[float, float] | None:
    """The two outline anchor params (root centre ± half span) for ``tip``.
    None for records without a procedural root."""

    centre = effective_tail_root_param(record, tip)
    half_px = tail_root_half_px(record)
    if centre is None or half_px is None:
        return None
    half = min(0.225, half_px / max(1e-6, outline_perimeter(record)))
    return ((centre - half) % 1.0, (centre + half) % 1.0)


def _axis_frame(record: BalloonRecord, tip: tuple[float, float]):
    """(u, v) unit vectors of the straight tail frame (along, across)."""

    mid = tail_anchor_mid(record, tip)
    if mid is None:
        return None
    ax, ay = tip[0] - mid[0], tip[1] - mid[1]
    length = math.hypot(ax, ay)
    if length < 1e-6:
        return None
    ux, uy = ax / length, ay / length
    return (ux, uy), (-uy, ux)


def tail_root_local_adjust(record: BalloonRecord, tip: tuple[float, float]) -> QTransform | None:
    """Local→local affine that moves the crop's base corners by their
    per-corner offsets (``tail_root_corner_offsets``), keeping the tip
    fixed. None when there are no offsets (plain axis-only stretch)."""

    offsets = record.tail_root_corner_offsets
    if offsets is None or record.tail_local_base is None or record.tail_local_tip is None:
        return None
    if all(abs(v) < 1e-6 for pt in offsets for v in pt):
        return None
    base_transform = _plain_tail_transform(record, tip)
    frame = _axis_frame(record, tip)
    if base_transform is None or frame is None:
        return None
    (ux, uy), (vx, vy) = frame
    local_a, local_b = record.tail_local_base
    local_tip = record.tail_local_tip
    page_pts = []
    for (lx, ly), (oa, ob) in zip((local_a, local_b), offsets, strict=True):
        pt = base_transform.map(QPointF(lx, ly))
        page_pts.append((pt.x() + ux * oa + vx * ob, pt.y() + uy * oa + vy * ob))
    tip_pt = base_transform.map(QPointF(*local_tip))
    page_pts.append((tip_pt.x(), tip_pt.y()))
    full = _affine_from_three_points((local_a, local_b, local_tip), page_pts)
    if full is None:
        return None
    inv, ok = base_transform.inverted()
    if not ok:
        return None
    # local' = T0^-1 (M (local))
    return full * inv


def tail_root_corners(
    record: BalloonRecord, tip: tuple[float, float]
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Page positions of the tail crop's two base corners as rendered
    (straight-axis frame; the base band is rigid even when bent)."""

    if record.tail_local_base is None:
        return None
    transform = tail_render_transform(record, tip)
    if transform is None:
        return None
    (ax, ay), (bx, by) = record.tail_local_base
    pa = transform.map(QPointF(ax, ay))
    pb = transform.map(QPointF(bx, by))
    return ((pa.x(), pa.y()), (pb.x(), pb.y()))


def tail_anchor_mid(record: BalloonRecord, tip: tuple[float, float]) -> tuple[float, float] | None:
    """Page point where the tail crop's base midpoint lands: the outline
    point at the root centre param (ON the outline — not the chord midpoint,
    which sinks inside on wide spans), pushed ``tail_base_gap`` px towards
    ``tip`` so a drawn balloon↔tail gap is kept constant while the tail
    moves."""

    centre = effective_tail_root_param(record, tip)
    if centre is None:
        return None
    mid = outline_point_at_param(record, centre)
    gap = float(record.tail_base_gap or 0.0)
    if abs(gap) < 1e-6:
        return mid
    dx, dy = tip[0] - mid[0], tip[1] - mid[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return mid
    return (mid[0] + dx / length * gap, mid[1] + dy / length * gap)


def tail_bend_control_point(
    record: BalloonRecord, base_mid: tuple[float, float], tip: tuple[float, float]
) -> tuple[float, float]:
    """Page-space position of the bend anchor for the given axis."""

    along, across = record.tail_bend if record.tail_bend is not None else (0.5, 0.0)
    ax, ay = tip[0] - base_mid[0], tip[1] - base_mid[1]
    length = math.hypot(ax, ay)
    if length < 1e-6:
        return base_mid
    ux, uy = ax / length, ay / length
    return (
        base_mid[0] + ux * length * along - uy * across,
        base_mid[1] + uy * length * along + ux * across,
    )


#: curve parameter at which the orange bend handle sits — chosen so that a
#: straight tail (bend (0.5, 0)) shows the handle at the axis midpoint
_BEND_HANDLE_T = 0.58


def _bend_bernstein(t: float) -> tuple[float, float, float, float]:
    return ((1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3)


def tail_bend_handle_point(
    record: BalloonRecord, base_mid: tuple[float, float], tip: tuple[float, float]
) -> tuple[float, float]:
    """Page position of the bend handle: a point ON the bent axis curve
    (not the control point, which floats off the curve)."""

    p1 = tail_bend_control_point(record, base_mid, tip)
    ax, ay = tip[0] - base_mid[0], tip[1] - base_mid[1]
    length = math.hypot(ax, ay)
    if length < 1e-6:
        return base_mid
    ux, uy = ax / length, ay / length
    c1 = (base_mid[0] + ux * length * 0.3, base_mid[1] + uy * length * 0.3)
    b0, b1, b2, b3 = _bend_bernstein(_BEND_HANDLE_T)
    return (
        b0 * base_mid[0] + b1 * c1[0] + b2 * p1[0] + b3 * tip[0],
        b0 * base_mid[1] + b1 * c1[1] + b2 * p1[1] + b3 * tip[1],
    )


def tail_bend_from_handle_point(
    record: BalloonRecord,
    base_mid: tuple[float, float],
    tip: tuple[float, float],
    point: tuple[float, float],
) -> tuple[float, float]:
    """Inverse of :func:`tail_bend_handle_point`: the stored bend whose
    curve passes through ``point`` at the handle parameter."""

    ax, ay = tip[0] - base_mid[0], tip[1] - base_mid[1]
    length = math.hypot(ax, ay)
    if length < 1e-6:
        return (0.5, 0.0)
    ux, uy = ax / length, ay / length
    c1 = (base_mid[0] + ux * length * 0.3, base_mid[1] + uy * length * 0.3)
    b0, b1, b2, b3 = _bend_bernstein(_BEND_HANDLE_T)
    control = (
        (point[0] - b0 * base_mid[0] - b1 * c1[0] - b3 * tip[0]) / b2,
        (point[1] - b0 * base_mid[1] - b1 * c1[1] - b3 * tip[1]) / b2,
    )
    return tail_bend_from_point(record, base_mid, tip, control)


def tail_bend_from_point(
    record: BalloonRecord,
    base_mid: tuple[float, float],
    tip: tuple[float, float],
    point: tuple[float, float],
) -> tuple[float, float]:
    """Inverse of :func:`tail_bend_control_point` (drag → stored bend)."""

    ax, ay = tip[0] - base_mid[0], tip[1] - base_mid[1]
    length = math.hypot(ax, ay)
    if length < 1e-6:
        return (0.5, 0.0)
    ux, uy = ax / length, ay / length
    dx, dy = point[0] - base_mid[0], point[1] - base_mid[1]
    along = max(0.05, min(0.95, (dx * ux + dy * uy) / length))
    across = -dx * uy + dy * ux
    return (along, across)


def tail_render_transform(record: BalloonRecord, tip: tuple[float, float]) -> QTransform | None:
    """The full crop->page transform of the procedural tail for ``tip``.

    Shared by the part-layer renderer and the drag ghost preview so what
    the user drags is exactly what gets committed. The root snaps to the
    body boundary (keeping any deliberately drawn gap) and the crop is
    stretched along the root->tip axis only; independent root-corner
    offsets (blue anchors) are applied as a local pre-adjust.
    """

    plain = _plain_tail_transform(record, tip)
    if plain is None:
        return None
    adjust = tail_root_local_adjust(record, tip)
    if adjust is None:
        return plain
    return adjust * plain


def _plain_tail_transform(record: BalloonRecord, tip: tuple[float, float]) -> QTransform | None:
    """Axis-only stretch crop->page transform (no per-corner offsets)."""

    if (
        (record.tail_root_param is not None or record.tail_base_params is not None)
        and record.tail_local_base is not None
        and record.tail_local_tip is not None
    ):
        # 2 接続点モード: 付け根の中点が外周アンカー間の中点に密着し、
        # 先端は tip ハンドルへ。伸縮は軸方向のみ (垂直スケール 1.0) なので
        # 付け根ハンドルを動かしても取り込んだピクセル比率は変わらない。
        dest_mid = tail_anchor_mid(record, tip) or (0.0, 0.0)
        local_a, local_b = record.tail_local_base
        local_mid = ((local_a[0] + local_b[0]) * 0.5, (local_a[1] + local_b[1]) * 0.5)
        if math.hypot(tip[0] - dest_mid[0], tip[1] - dest_mid[1]) < 4.0:
            tip = (dest_mid[0], dest_mid[1] + 4.0)
        transform = _axis_stretch_transform(local_mid, record.tail_local_tip, dest_mid, tip)
        if transform is not None:
            return transform
    if record.tail_local_root is None or record.tail_local_tip is None:
        return None
    root = tail_root_on_body(record, tip)
    provenance = record.metadata.get("balloonize")
    gap = 0.0
    if isinstance(provenance, Mapping):
        try:
            gap = float(provenance.get("body_tail_gap_px") or 0.0)
        except (TypeError, ValueError):
            gap = 0.0
    if gap > 0.5:
        x, y, w, h = record.body_rect
        cx, cy = x + w * 0.5, y + h * 0.5
        length = math.hypot(tip[0] - cx, tip[1] - cy)
        if length > 1e-6:
            ux, uy = (tip[0] - cx) / length, (tip[1] - cy) / length
            root = (root[0] + ux * gap, root[1] + uy * gap)
    # avoid a fully degenerate tail when the tip sits on the boundary
    if math.hypot(tip[0] - root[0], tip[1] - root[1]) < 4.0:
        direction = math.atan2(tip[1] - root[1], tip[0] - root[0]) if tip != root else 0.0
        tip = (root[0] + 4.0 * math.cos(direction), root[1] + 4.0 * math.sin(direction))
    return _axis_stretch_transform(record.tail_local_root, record.tail_local_tip, root, tip)


def _bend_axis_curve(p0, p1, p2, count: int):
    """Sample the bent tail axis: a cubic whose START tangent is the straight
    base→tip axis (so the base band leaves the balloon rigidly, like the
    template balloon), pulled towards the bend control ``p1``, ending at the
    tip. Returns (xs, ys) numpy arrays of ``count + 1`` points."""

    import numpy as np  # noqa: PLC0415

    ax, ay = p2[0] - p0[0], p2[1] - p0[1]
    length = math.hypot(ax, ay)
    if length < 1e-6:
        ux, uy = 0.0, 1.0
    else:
        ux, uy = ax / length, ay / length
    c1 = (p0[0] + ux * length * 0.3, p0[1] + uy * length * 0.3)
    ts = np.linspace(0.0, 1.0, count + 1)
    b0 = (1 - ts) ** 3
    b1 = 3 * (1 - ts) ** 2 * ts
    b2 = 3 * (1 - ts) * ts**2
    b3 = ts**3
    xs = b0 * p0[0] + b1 * c1[0] + b2 * p1[0] + b3 * p2[0]
    ys = b0 * p0[1] + b1 * c1[1] + b2 * p1[1] + b3 * p2[1]
    return xs, ys


def _bend_warp_tail(  # noqa: PLR0911, PLR0912, PLR0915 - one warp pipeline
    painter: QPainter, crop: QImage, record: BalloonRecord
) -> bool:
    """Bent tail: warp the crop along a quadratic bezier axis (base mid →
    bend anchor → tip). Along-axis stretch only; the across-axis keeps the
    imported pixel ratio. Returns False when no bend applies."""

    if record.tail_bend is None or abs(record.tail_bend[1]) < 0.5:
        return False
    if (
        record.tail_base_params is None
        or record.tail_local_base is None
        or record.tail_local_tip is None
    ):
        return False
    try:
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
    except Exception:  # pragma: no cover
        return False
    from loomstroke.core.document_snapshot_builder import (  # noqa: PLC0415
        DocumentSnapshotBuilder,
    )

    tip = record.tail.target
    p0 = tail_anchor_mid(record, tip) or tip
    p2 = tip
    p1 = tail_bend_control_point(record, p0, p2)
    local_a, local_b = record.tail_local_base
    lm = ((local_a[0] + local_b[0]) * 0.5, (local_a[1] + local_b[1]) * 0.5)
    lt = record.tail_local_tip
    lax, lay = lt[0] - lm[0], lt[1] - lm[1]
    local_len = math.hypot(lax, lay)
    if local_len < 1e-6:
        return False
    lux, luy = lax / local_len, lay / local_len

    src = np.ascontiguousarray(DocumentSnapshotBuilder.qimage_to_array(crop))
    src_h, src_w = src.shape[:2]
    # local (s along axis in px, r across in px) for every crop pixel
    ys, xs = np.mgrid[0:src_h, 0:src_w].astype(np.float64)
    dx = xs - lm[0]
    dy = ys - lm[1]
    r_local = -dx * luy + dy * lux
    # curve length (approx) for the along-axis stretch factor
    bx, by = _bend_axis_curve(p0, p1, p2, 48)
    seg = np.hypot(np.diff(bx), np.diff(by))
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    curve_len = float(cum[-1])
    if curve_len < 4.0:
        return False
    stretch = curve_len / local_len

    # destination bbox: bezier extents ± max |r|
    r_abs_max = float(np.abs(r_local).max()) + 2.0
    x0 = math.floor(min(bx.min(), p0[0], p2[0]) - r_abs_max)
    y0 = math.floor(min(by.min(), p0[1], p2[1]) - r_abs_max)
    x1 = math.ceil(max(bx.max(), p0[0], p2[0]) + r_abs_max)
    y1 = math.ceil(max(by.max(), p0[1], p2[1]) + r_abs_max)
    dst_w, dst_h = max(1, x1 - x0), max(1, y1 - y0)
    if dst_w * dst_h > 4_000_000:
        return False

    # inverse map: for each dest pixel find the closest point ON the curve
    # (true segment-projection over a dense polyline), then (s, r) → source
    # pixel. Beyond the first/last segment the projection extends freely
    # OUTWARD so the crop's ends beyond base/tip survive; anywhere else the
    # foot is clamped to its segment (no folding on the concave side, no far
    # extrapolation → no streaks). Using the real foot instead of the
    # nearest sample keeps the WIDE tail base attached: a pixel behind the
    # base but far off-axis projects onto segment 0 (s < 0), never onto a
    # later segment.
    dense = 192
    dbx, dby = _bend_axis_curve(p0, p1, p2, dense)
    dseg = np.maximum(1e-9, np.hypot(np.diff(dbx), np.diff(dby)))
    dcum = np.concatenate([[0.0], np.cumsum(dseg)])
    gy, gx = np.mgrid[0:dst_h, 0:dst_w].astype(np.float64)
    gx += x0
    gy += y0
    best_d = np.full(gx.shape, np.inf)
    best_s = np.zeros(gx.shape)
    best_r = np.zeros(gx.shape)
    for k in range(dense):
        ex = (dbx[k + 1] - dbx[k]) / dseg[k]
        ey = (dby[k + 1] - dby[k]) / dseg[k]
        vx = gx - dbx[k]
        vy = gy - dby[k]
        proj = vx * ex + vy * ey
        if k == 0 and k == dense - 1:
            foot = proj
        elif k == 0:
            foot = np.minimum(proj, dseg[k])
        elif k == dense - 1:
            foot = np.maximum(proj, 0.0)
        else:
            foot = np.clip(proj, 0.0, dseg[k])
        fx = dbx[k] + ex * foot
        fy = dby[k] + ey * foot
        d = np.hypot(gx - fx, gy - fy)
        take = d < best_d
        best_d = np.where(take, d, best_d)
        best_s = np.where(take, dcum[k] + foot, best_s)
        best_r = np.where(take, -vx * ey + vy * ex, best_r)
    # RIGID base band: the first part of the tail (where it meets the
    # balloon) never bends — its pixels use the straight base→tip frame at
    # p0, so the wide base can't be captured by a later, curving segment and
    # the join stays exactly where the straight tail put it. Short blend
    # into the curved mapping beyond the band.
    aux, auy = p2[0] - p0[0], p2[1] - p0[1]
    alen = math.hypot(aux, auy)
    if alen > 1e-6:
        aux, auy = aux / alen, auy / alen
        gvx = gx - p0[0]
        gvy = gy - p0[1]
        s_rig = gvx * aux + gvy * auy
        r_rig = -gvx * auy + gvy * aux
        # rigid band = about the root span (absolute px, clamped to a share
        # of the tail): a short tail keeps most of its length bendable, a
        # long one still leaves the balloon straight for a while
        half_root = tail_root_half_px(record) or (curve_len * 0.1)
        band = min(0.3 * curve_len, max(6.0, half_root * 1.2))
        margin = min(0.2 * curve_len, max(4.0, band * 0.6))  # blend → no crease
        w_rig = np.clip((band + margin - s_rig) / (2.0 * margin), 0.0, 1.0)
        best_s = w_rig * s_rig + (1.0 - w_rig) * best_s
        best_r = w_rig * r_rig + (1.0 - w_rig) * best_r
    src_s = best_s / stretch
    map_x = lm[0] + lux * src_s - luy * best_r
    map_y = lm[1] + luy * src_s + lux * best_r
    adjust = tail_root_local_adjust(record, tip)
    if adjust is not None:
        inv, ok = adjust.inverted()
        if ok:
            # source pixel = S^-1(local'), S = per-corner root adjust
            m11, m12, m21, m22 = inv.m11(), inv.m12(), inv.m21(), inv.m22()
            dx_, dy_ = inv.dx(), inv.dy()
            map_x, map_y = (
                m11 * map_x + m21 * map_y + dx_,
                m12 * map_x + m22 * map_y + dy_,
            )
    map_x = map_x.astype(np.float32)
    map_y = map_y.astype(np.float32)
    warped = cv2.remap(
        src,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    warped = np.ascontiguousarray(warped)
    out = QImage(warped.data, dst_w, dst_h, dst_w * 4, QImage.Format.Format_RGBA8888)
    painter.drawImage(
        QPoint(x0, y0), out.copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    )
    return True


def _blit_procedural_tail(painter: QPainter, crop: QImage, record: BalloonRecord) -> bool:
    """Ellipse-template-like tail: base on the body boundary, point at
    ``tail.target``, axis-only stretch (imported width ratio preserved)."""

    if _bend_warp_tail(painter, crop, record):
        return True
    transform = tail_render_transform(record, record.tail.target)
    if transform is None:
        return False
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setTransform(transform, combine=False)
    painter.drawImage(QPoint(0, 0), crop)
    painter.restore()
    return True


def _silhouette_fill(mask_u8) -> object | None:
    """Filled silhouette (largest closed region) of a coverage mask."""

    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    if not mask_u8.any():
        return None
    h, w = mask_u8.shape
    kernel_size = max(3, round(min(h, w) * 0.03) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    closed = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, kernel)
    contours, _hier = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    fill = np.zeros_like(mask_u8)
    cv2.fillPoly(fill, [max(contours, key=cv2.contourArea)], 255)
    return fill


def _signed_distance(fill_u8):
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    inside = cv2.distanceTransform(fill_u8, cv2.DIST_L2, 5)
    outside = cv2.distanceTransform(255 - fill_u8, cv2.DIST_L2, 5)
    return inside.astype(np.float32) - outside.astype(np.float32)


def _edge_preserving_body_image(
    crop: QImage,
    outline_local: list[tuple[float, float]],
    dst_w: int,
    dst_h: int,
) -> QImage | None:
    """Resize the hand-drawn body WITHOUT scaling its outline stroke.

    SDF-gradient correction: start from the PLAIN scale (which preserves the
    drawn shape and the along-boundary texture exactly), then, near the
    silhouette, nudge each sample along the SOURCE signed-distance-field
    gradient so its source distance-to-edge equals the true destination
    distance. No boundary correspondence, no label jumps — a smooth,
    continuous field — so the stroke keeps its imported width with the
    original silhouette intact. Falls back to None on degenerate masks.
    """

    del outline_local  # the crop's own alpha is the authority
    try:
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
    except Exception:  # pragma: no cover - environment guard
        return None
    if dst_w < 8 or dst_h < 8:
        return None
    from loomstroke.core.document_snapshot_builder import (  # noqa: PLC0415
        DocumentSnapshotBuilder,
    )

    src = np.ascontiguousarray(DocumentSnapshotBuilder.qimage_to_array(crop))
    src_h, src_w = src.shape[:2]
    scale_x = dst_w / max(1.0, float(src_w))
    scale_y = dst_h / max(1.0, float(src_h))

    src_mask = ((src[:, :, 3] > 8).astype(np.uint8)) * 255
    src_fill = _silhouette_fill(src_mask)
    if src_fill is None:
        return None
    d_src = _signed_distance(src_fill)
    # smooth gradient field of the source SDF
    d_blur = cv2.GaussianBlur(d_src, (0, 0), 1.2)
    gy, gx = np.gradient(d_blur.astype(np.float64))
    g_norm = np.maximum(1e-6, np.hypot(gx, gy))
    gx = (gx / g_norm).astype(np.float32)
    gy = (gy / g_norm).astype(np.float32)

    dst_fill = cv2.resize(src_fill, (dst_w, dst_h), interpolation=cv2.INTER_NEAREST)
    d_dst = _signed_distance(dst_fill)

    # plain-scale sample positions (shape-perfect baseline)
    ys, xs = np.mgrid[0:dst_h, 0:dst_w].astype(np.float32)
    lin_x = xs / scale_x
    lin_y = ys / scale_y

    # source SDF value/gradient at the plain positions
    s_at = cv2.remap(d_src, lin_x, lin_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    gx_at = cv2.remap(gx, lin_x, lin_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    gy_at = cv2.remap(gy, lin_x, lin_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    # inside the band: match true dest distance; deep interior: no correction
    band = 24.0
    blend_end = band * 2.0
    weight = np.clip(1.0 - (np.abs(d_dst) - band) / max(1.0, blend_end - band), 0.0, 1.0)
    target = s_at + (d_dst - s_at) * weight.astype(np.float32)
    delta = target - s_at
    map_x = lin_x + gx_at * delta
    map_y = lin_y + gy_at * delta
    warped = cv2.remap(
        src,
        map_x.astype(np.float32),
        map_y.astype(np.float32),
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    warped = np.ascontiguousarray(warped)
    warped[d_dst < -6.0] = 0
    out = QImage(warped.data, dst_w, dst_h, dst_w * 4, QImage.Format.Format_RGBA8888)
    return out.copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def detect_body_opening(  # noqa: PLR0911, PLR0912, PLR0915 - one detection pipeline
    body_alpha_u8, outline_points: list[tuple[float, float]]
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Endpoints (crop-local px) of the largest gap in an OPEN outline stroke.

    Walks the closed silhouette contour and finds the longest run of contour
    samples that has no drawn ink nearby — that run is the opening; its two
    ends are the stroke endpoints. Returns None for closed outlines.
    """

    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    ink = ((body_alpha_u8 > 8).astype(np.uint8)) * 255
    # A large opening defeats the small morphological close, so build the
    # walk path from the CONVEX HULL of the ink (a closed loop that spans
    # the gap) and sample it densely; the uncovered run is the opening.
    ys_, xs_ = np.nonzero(ink)
    if xs_.size < 16:
        return None
    hull = cv2.convexHull(np.stack([xs_, ys_], axis=1).astype(np.int32)).reshape(-1, 2)
    if len(hull) < 3:
        return None
    dense: list[tuple[float, float]] = []
    for i in range(len(hull)):
        ax, ay = hull[i]
        bx, by = hull[(i + 1) % len(hull)]
        steps = max(1, int(math.hypot(bx - ax, by - ay) / 2.0))
        for s in range(steps):
            f = s / steps
            dense.append((ax + (bx - ax) * f, ay + (by - ay) * f))
    outline_points = dense
    if len(outline_points) < 8:
        return None
    # tolerance ~ stroke width: dilate ink so contour samples ON the stroke
    # count as covered even with AA/rounding
    # hull samples can sit a few px outside the actual stroke on concave
    # spans; a generous dilation keeps drawn spans "covered" while a real
    # opening (tens of px) stays uncovered
    ink_near = cv2.dilate(ink, np.ones((15, 15), np.uint8))
    h, w = ink_near.shape
    covered = []
    for px, py in outline_points:
        x = round(px)
        y = round(py)
        inside = 0 <= x < w and 0 <= y < h
        covered.append(bool(inside and ink_near[y, x] > 0))
    n = len(covered)
    if all(covered):
        return None
    # longest circular run of uncovered samples
    best_len = 0
    best_start = -1
    start = -1
    for i in range(2 * n):
        c = covered[i % n]
        if not c:
            if start < 0:
                start = i
            run = i - start + 1
            if run > best_len:
                best_len = run
                best_start = start
        else:
            start = -1
        if best_len >= n:
            break
    if best_len < max(4, n // 40) or best_len >= n - 4:
        return None
    a = outline_points[best_start % n]
    b = outline_points[(best_start + best_len - 1) % n]
    # A closed but CONCAVE outline also leaves hull spans uncovered. Confirm
    # a real opening by checking the ink is TOPOLOGICALLY open: the ink's
    # own skeleton must have free ends near both candidate points. Cheap
    # proxy: flood-filling the background from outside must reach the
    # interior (a closed stroke seals it).
    h_, w_ = ink.shape
    closed_small = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    padded = np.zeros((h_ + 2, w_ + 2), dtype=np.uint8)
    padded[1:-1, 1:-1] = closed_small
    flood = padded.copy()
    ff_mask = np.zeros((h_ + 4, w_ + 4), dtype=np.uint8)
    cv2.floodFill(flood, ff_mask, (0, 0), 255)
    interior = cv2.bitwise_not(flood)[1:-1, 1:-1]
    interior_area = int(np.count_nonzero(cv2.bitwise_and(interior, cv2.bitwise_not(ink))))
    hull_area = float(cv2.contourArea(hull.astype(np.float32)))
    if hull_area <= 0.0:
        return None
    # a sealed outline encloses most of its hull; an open one encloses ~0
    if interior_area > 0.35 * hull_area:
        return None

    # snap the candidate ends to the nearest actual ink pixels (stroke tips)
    def _snap(pt):
        d = np.hypot(xs_ - pt[0], ys_ - pt[1])
        k = int(np.argmin(d))
        return (float(xs_[k]), float(ys_[k]))

    return (_snap(a), _snap(b))


def _stroke_end_tangent(ink_u8, end: tuple[float, float], radius: float):
    """(unit tangent pointing OUT of the ink at ``end``, local half-width).
    Tangent = principal axis of the ink pixels within ``radius`` of the end,
    oriented from their centroid towards the end."""

    import numpy as np  # noqa: PLC0415

    h, w = ink_u8.shape
    x0 = max(0, int(end[0] - radius))
    x1 = min(w, int(end[0] + radius) + 1)
    y0 = max(0, int(end[1] - radius))
    y1 = min(h, int(end[1] + radius) + 1)
    sub = ink_u8[y0:y1, x0:x1]
    ys, xs = np.nonzero(sub)
    if len(xs) < 4:
        return None
    xs = xs.astype(np.float64) + x0
    ys = ys.astype(np.float64) + y0
    keep = np.hypot(xs - end[0], ys - end[1]) <= radius
    xs, ys = xs[keep], ys[keep]
    if len(xs) < 4:
        return None
    cx, cy = float(xs.mean()), float(ys.mean())
    cov = np.cov(np.stack([xs - cx, ys - cy]))
    vals, vecs = np.linalg.eigh(cov)
    ux, uy = float(vecs[0, -1]), float(vecs[1, -1])
    # orient towards the end (= out of the ink)
    if (end[0] - cx) * ux + (end[1] - cy) * uy < 0:
        ux, uy = -ux, -uy
    # local width: spread along the minor axis
    minor = float(math.sqrt(max(vals[0], 0.0)))
    return (ux, uy), max(0.75, minor * 2.0)


def gap_bridge_curve(
    ink_u8,
    end_a: tuple[float, float],
    end_b: tuple[float, float],
    step_px: float = 2.0,
    min_align: float = -0.2,
    tangent_scale: float = 1.0,
) -> list[tuple[float, float]]:
    """Cubic-Hermite bridge from ``end_a`` to ``end_b`` whose end tangents
    continue the drawn stroke on each side — the natural closure of an open
    balloon outline (a curved arc, not a chord). Falls back to a straight
    chord when the ends have no usable ink around them."""

    chord = math.hypot(end_b[0] - end_a[0], end_b[1] - end_a[1])
    if chord < 1e-6:
        return [end_a, end_b]
    radius = max(12.0, min(48.0, chord * 0.6))
    ta = _stroke_end_tangent(ink_u8, end_a, radius)
    tb = _stroke_end_tangent(ink_u8, end_b, radius)
    if ta is None or tb is None:
        return [end_a, end_b]
    (ax, ay), _wa = ta
    (bx, by), _wb = tb
    # a tangent that points across the gap should not swing back; keep only
    # ends whose out-direction roughly agrees with the chord direction
    cx, cy = (end_b[0] - end_a[0]) / chord, (end_b[1] - end_a[1]) / chord
    if ax * cx + ay * cy < min_align:
        ax, ay = cx, cy
    if -(bx * cx + by * cy) < min_align:
        bx, by = -cx, -cy
    m0 = (ax * chord * tangent_scale, ay * chord * tangent_scale)
    # arriving tangent at B = into the ink
    m1 = (-bx * chord * tangent_scale, -by * chord * tangent_scale)
    count = max(2, int(chord * 1.5 / step_px))
    pts: list[tuple[float, float]] = []
    for k in range(count + 1):
        s = k / count
        h00 = 2 * s**3 - 3 * s**2 + 1
        h10 = s**3 - 2 * s**2 + s
        h01 = -2 * s**3 + 3 * s**2
        h11 = s**3 - s**2
        pts.append(
            (
                h00 * end_a[0] + h10 * m0[0] + h01 * end_b[0] + h11 * m1[0],
                h00 * end_a[1] + h10 * m0[1] + h01 * end_b[1] + h11 * m1[1],
            )
        )
    return pts


def closed_outline_for_open_stroke(
    body_alpha_u8, gap_ends: tuple[tuple[float, float], tuple[float, float]] | None = None
) -> list[tuple[float, float]]:
    """A closed perimeter for an OPEN outline stroke, crop-local.

    The drawn spans keep their shape; the gap is sealed with a curved
    Hermite bridge continuing the stroke ends, then the outer contour is
    traced. Falls back to the ink convex hull when the
    ends are unknown or the seal fails.
    """

    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    ink = ((body_alpha_u8 > 8).astype(np.uint8)) * 255
    if not ink.any():
        return []
    sealed = ink.copy()
    if gap_ends is not None:
        # bridge stroke ~ typical line width; generous so AA gaps close.
        # The bridge continues the stroke's own curvature (Hermite), so the
        # perimeter across the gap looks like the balloon, not a chord.
        bridge = gap_bridge_curve(ink, gap_ends[0], gap_ends[1])
        for (ax, ay), (bx, by) in itertools.pairwise(bridge):
            cv2.line(sealed, (round(ax), round(ay)), (round(bx), round(by)), 255, 9)
    fill = _silhouette_fill(sealed)
    contour = None
    if fill is not None:
        contours, _hier = cv2.findContours(fill, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if contours:
            contour = max(contours, key=cv2.contourArea)
            # sanity: sealed shape must be substantially larger than the
            # bare ink (else the seal failed and we only traced the line)
            if float(cv2.contourArea(contour)) < 1.6 * float(np.count_nonzero(ink)):
                contour = None
    if contour is None:
        ys_, xs_ = np.nonzero(ink)
        contour = cv2.convexHull(np.stack([xs_, ys_], axis=1).astype(np.int32))
    perimeter = float(cv2.arcLength(contour, closed=True))
    epsilon = min(6.0, max(0.75, perimeter * 0.0035))
    simplified = cv2.approxPolyDP(contour, epsilon, closed=True).reshape(-1, 2)
    if len(simplified) < 8:
        simplified = contour.reshape(-1, 2)
    return [(float(px), float(py)) for px, py in simplified]


def _outline_arc_params(outline_points: list[tuple[float, float]]) -> list[float]:
    total = 0.0
    lengths = []
    n = len(outline_points)
    for i in range(n):
        ax, ay = outline_points[i]
        bx, by = outline_points[(i + 1) % n]
        seg = math.hypot(bx - ax, by - ay)
        lengths.append(seg)
        total += seg
    params = []
    acc = 0.0
    for seg in lengths:
        params.append(acc / max(1e-9, total))
        acc += seg
    return params


def _snap_to_ink(
    ink_u8, point: tuple[float, float], radius: float, dist=None
) -> tuple[float, float]:
    """Ink pixel near ``point`` (within ``radius``), preferring the stroke
    CENTRELINE when a distance transform ``dist`` is given; else ``point``."""

    import numpy as np  # noqa: PLC0415

    h, w = ink_u8.shape
    x0 = max(0, int(point[0] - radius))
    x1 = min(w, int(point[0] + radius) + 1)
    y0 = max(0, int(point[1] - radius))
    y1 = min(h, int(point[1] + radius) + 1)
    ys, xs = np.nonzero(ink_u8[y0:y1, x0:x1])
    if len(xs) == 0:
        return point
    xs = xs + x0
    ys = ys + y0
    d = np.hypot(xs - point[0], ys - point[1])
    inside = d <= radius
    if not inside.any():
        return point
    score = d.astype(np.float64)
    if dist is not None:
        score = score - 2.0 * dist[ys, xs]
    score = np.where(inside, score, np.inf)
    k = int(np.argmin(score))
    return (float(xs[k]), float(ys[k]))


def _polyline_frames(pts):
    """(P[n,2], cum[n], T[n,2] unit tangents) for a polyline."""

    import numpy as np  # noqa: PLC0415

    P = np.asarray(pts, dtype=np.float64)  # noqa: N806
    seg = np.hypot(np.diff(P[:, 0]), np.diff(P[:, 1]))
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    T = np.gradient(P, axis=0)  # noqa: N806
    ln = np.maximum(1e-9, np.hypot(T[:, 0], T[:, 1]))
    T /= ln[:, None]
    return P, cum, T


def _core_mask(alpha_u8):
    """Half-coverage mask (alpha >= 128): its width IS the visual line width
    (the AA fringe of a drawn line sits at < 50% coverage)."""

    import numpy as np  # noqa: PLC0415

    return ((alpha_u8 >= 128).astype(np.uint8)) * 255


def _roi_of(mask_u8, pad: int):
    """(y0, y1, x0, x1) bounding box of the nonzero pixels, padded/clamped."""

    import numpy as np  # noqa: PLC0415

    ys, xs = np.nonzero(mask_u8)
    if len(xs) == 0:
        return None
    h, w = mask_u8.shape
    return (
        max(0, int(ys.min()) - pad),
        min(h, int(ys.max()) + pad + 1),
        max(0, int(xs.min()) - pad),
        min(w, int(xs.max()) + pad + 1),
    )


def _distance_transform_roi(mask_u8, roi=None):
    """Distance transform of ``mask_u8`` computed only inside ``roi`` (the
    ink's bounding box + padding when None); zeros elsewhere. The masks are
    canvas-sized but the ink covers a small part of it."""

    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    out = np.zeros(mask_u8.shape, np.float32)
    if roi is None:
        roi = _roi_of(mask_u8, 8)
    if roi is None:
        return out
    y0, y1, x0, x1 = roi
    if y1 <= y0 or x1 <= x0:
        return out
    out[y0:y1, x0:x1] = cv2.distanceTransform(
        np.ascontiguousarray(mask_u8[y0:y1, x0:x1]), cv2.DIST_L2, 3
    )
    return out


def _ridge_width(dist, region_mask, fallback: float) -> float:
    """Line width from the distance-transform RIDGE (local maxima = the
    stroke centreline) inside ``region_mask``: 2 x median ridge radius.
    Robust to the AA fringe and to a few thick blobs."""

    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    roi = _roi_of(region_mask, 2)
    if roi is None:
        return fallback
    y0, y1, x0, x1 = roi
    d = dist[y0:y1, x0:x1]
    m = region_mask[y0:y1, x0:x1] > 0
    if not m.any():
        return fallback
    local_max = cv2.dilate(d, np.ones((3, 3), np.uint8))
    ridge = m & (d > 0) & (d >= local_max - 1e-3)
    vals = d[ridge]
    if vals.size == 0:
        vals = d[m & (d > 0)]
    if vals.size == 0:
        return fallback
    # DT of a binary line of width w peaks at ~w/2 (measured to the first
    # background pixel), so width ~ 2 x ridge radius
    return max(1.0, 2.0 * float(np.median(vals)))


def _local_stroke_width(
    dist, point: tuple[float, float], radius: float, fallback: float, core=None
) -> float:
    """Line width around ``point``: ridge width of the (core) ink within
    ``radius``; ``fallback`` when nothing is nearby."""

    import numpy as np  # noqa: PLC0415

    h, w = dist.shape
    x0 = max(0, int(point[0] - radius))
    x1 = min(w, int(point[0] + radius) + 1)
    y0 = max(0, int(point[1] - radius))
    y1 = min(h, int(point[1] + radius) + 1)
    if x1 <= x0 or y1 <= y0:
        return fallback
    region = np.zeros(dist.shape, np.uint8)
    if core is not None:
        region[y0:y1, x0:x1] = (core[y0:y1, x0:x1] > 0).astype(np.uint8) * 255
    else:
        region[y0:y1, x0:x1] = (dist[y0:y1, x0:x1] > 0).astype(np.uint8) * 255
    return _ridge_width(dist, region, fallback)


def _region_stroke_width(dist, region_mask, fallback: float) -> float:
    """Line width of the ink inside ``region_mask`` (ridge estimator)."""

    import numpy as np  # noqa: PLC0415

    region = (np.asarray(region_mask) > 0).astype(np.uint8) * 255
    return _ridge_width(dist, region, fallback)


def _project_onto_polyline(gx, gy, P, cum, T):  # noqa: N803
    """(s along, r across) of every grid point w.r.t. the polyline frames
    from :func:`_polyline_frames` (nearest sample + its tangent)."""

    import numpy as np  # noqa: PLC0415

    best_d = np.full(gx.shape, np.inf)
    best_k = np.zeros(gx.shape, dtype=np.int64)
    for k in range(len(P)):
        d = np.hypot(gx - P[k, 0], gy - P[k, 1])
        take = d < best_d
        best_d = np.where(take, d, best_d)
        best_k = np.where(take, k, best_k)
    vx = gx - P[best_k, 0]
    vy = gy - P[best_k, 1]
    tx, ty = T[best_k, 0], T[best_k, 1]
    s = cum[best_k] + vx * tx + vy * ty
    r = -vx * ty + vy * tx
    return s, r


def _paint_stroke(target_u8, polyline, width_start: float, width_end: float) -> None:
    """Paint an anti-aliased stroke of EXACT width (tapering linearly from
    ``width_start`` to ``width_end``, round caps) along ``polyline`` into
    ``target_u8`` (max-composited coverage 0..255). cv2.line's integer
    thickness renders 1-2 px fatter than asked, so lines are rasterised
    here from the distance to the centreline instead."""

    import numpy as np  # noqa: PLC0415

    if len(polyline) < 2:
        return
    P, cum, T = _polyline_frames(polyline)  # noqa: N806
    length = float(cum[-1])
    if length < 1e-6:
        return
    h, w = target_u8.shape
    w_max = max(width_start, width_end)
    pad = int(w_max * 0.5 + 3)
    x0 = max(0, int(P[:, 0].min()) - pad)
    x1 = min(w, int(P[:, 0].max()) + pad + 1)
    y0 = max(0, int(P[:, 1].min()) - pad)
    y1 = min(h, int(P[:, 1].max()) + pad + 1)
    if x1 <= x0 or y1 <= y0:
        return
    gy, gx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
    s, r = _project_onto_polyline(gx, gy, P, cum, T)
    beyond = np.maximum(0.0, np.maximum(-s, s - length))
    dist = np.hypot(r, beyond)
    f = np.clip(s / length, 0.0, 1.0)
    half = (width_start + (width_end - width_start) * f) * 0.5
    coverage = np.clip(half + 0.5 - dist, 0.0, 1.0)
    patch = (coverage * 255.0).astype(np.uint8)
    target_u8[y0:y1, x0:x1] = np.maximum(target_u8[y0:y1, x0:x1], patch)


def _transplant_strip(  # noqa: PLR0913, PLR0915 - one remap pipeline
    rgba,
    strip_mask,
    src_axis,
    dst_axis,
    half_width: float,
    width_from: float | None = None,
    width_to: tuple[float, float] | None = None,
):
    """Move the ink under ``strip_mask`` from along ``src_axis`` to along
    ``dst_axis`` (arc-length remap, across-offset kept). With ``width_from``
    (the strip's own line width) and ``width_to`` (target width at the two
    ends of ``dst_axis``) the across-profile is scaled so the moved line is
    as thick as the ends it joins. Returns a full-size RGBA array (zeros
    elsewhere) or None when there is nothing to move."""

    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    if len(src_axis) < 2 or len(dst_axis) < 2:
        return None
    ys, xs = np.nonzero(strip_mask)
    if len(xs) < 8 or not (rgba[ys, xs, 3] > 8).any():
        return None
    h, w = strip_mask.shape
    sp, scum, st = _polyline_frames(src_axis)
    dp, dcum, dt = _polyline_frames(dst_axis)
    src_len = float(scum[-1])
    dst_len = float(dcum[-1])
    if src_len < 1e-6 or dst_len < 1e-6:
        return None

    _sr = _project_onto_polyline

    # centreline correction: the strip's ink sits at some across-offset from
    # the (contour-based) source axis; measure it so it lands centred on the
    # destination bridge (which IS a centreline)
    ink_sel = rgba[ys, xs, 3] >= 128  # core pixels → the stroke centreline
    if not ink_sel.any():
        ink_sel = rgba[ys, xs, 3] > 8
    s_ink, r_ink = _sr(xs[ink_sel].astype(np.float64), ys[ink_sel].astype(np.float64), sp, scum, st)
    # per-s centreline offset (binned median of r along the strip): the
    # source axis is the outer contour whose curvature differs from the
    # stroke's, so a single global offset would leave the transplanted line
    # wobbling +-1-2px around the bridge and reading thicker than it is
    n_bins = max(3, int(src_len / 4.0))
    edges = np.linspace(0.0, src_len, n_bins + 1)
    centres = (edges[:-1] + edges[1:]) * 0.5
    bin_idx = np.clip(np.searchsorted(edges, s_ink, side="right") - 1, 0, n_bins - 1)
    bin_r = np.full(n_bins, np.nan)
    for b in range(n_bins):
        sel = bin_idx == b
        if sel.any():
            bin_r[b] = float(np.median(r_ink[sel]))
    have = ~np.isnan(bin_r)
    bin_r = np.interp(centres, centres[have], bin_r[have]) if have.any() else np.zeros(n_bins)
    if n_bins >= 3:
        # light smoothing
        bin_r = np.convolve(np.pad(bin_r, 1, mode="edge"), np.ones(3) / 3.0, mode="valid")

    pad = int(half_width * 2 + 4)
    x0 = max(0, int(dp[:, 0].min()) - pad)
    x1 = min(w, int(dp[:, 0].max()) + pad + 1)
    y0 = max(0, int(dp[:, 1].min()) - pad)
    y1 = min(h, int(dp[:, 1].max()) + pad + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    gy, gx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
    s_d, r_d = _sr(gx, gy, dp, dcum, dt)
    valid = (s_d >= -half_width) & (s_d <= dst_len + half_width) & (np.abs(r_d) <= half_width * 2)
    s_src = np.clip(s_d / dst_len, 0.0, 1.0) * src_len
    # source point at arc-length s_src along src axis, offset r + r_shift
    idx = np.searchsorted(scum, s_src, side="right") - 1
    idx = np.clip(idx, 0, len(sp) - 2)
    seg_len = np.maximum(1e-9, scum[idx + 1] - scum[idx])
    f = (s_src - scum[idx]) / seg_len
    px = sp[idx, 0] + (sp[idx + 1, 0] - sp[idx, 0]) * f
    py = sp[idx, 1] + (sp[idx + 1, 1] - sp[idx, 1]) * f
    tx = st[idx, 0] + (st[idx + 1, 0] - st[idx, 0]) * f
    ty = st[idx, 1] + (st[idx + 1, 1] - st[idx, 1]) * f
    tl = np.maximum(1e-9, np.hypot(tx, ty))
    tx /= tl
    ty /= tl
    # width matching: dest across-offset r_d corresponds to source offset
    # r_d x (strip width / target width at this s)
    r_shift = np.interp(s_src, centres, bin_r)
    if width_from and width_to and width_from > 0.5:
        f_s = np.clip(s_d / dst_len, 0.0, 1.0)
        target_w = width_to[0] + (width_to[1] - width_to[0]) * f_s
        ratio = width_from / np.maximum(1.0, target_w)
        ratio = np.clip(ratio, 0.5, 2.0)
        rr = r_d * ratio + r_shift
    else:
        rr = r_d + r_shift
    map_x = (px - ty * rr).astype(np.float32)
    map_y = (py + tx * rr).astype(np.float32)
    src = rgba.copy()
    src[strip_mask == 0] = 0
    patch = cv2.remap(
        src,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    patch[~valid] = 0
    out = np.zeros_like(rgba)
    out[y0:y1, x0:x1] = patch
    return out


def _warp_body_opening(  # noqa: PLR0911, PLR0912, PLR0915 - fill/cut pipeline
    image: QImage,
    record: BalloonRecord,
    tip: tuple[float, float],
) -> QImage:
    """Procedural opening — WITHOUT moving the balloon's line at all.

    ``record.body_open`` True: the outline is CUT open around the effective
    tail base (arc = the drawn gap's length, or the tail base span for a
    closed drawing). A gap drawn elsewhere (``body_opening_params``) is
    always FILLED so exactly one opening remains — or none when
    ``body_open`` is False (drawn-open balloon rendered closed).

    Fill-and-cut: the old gap is closed by cloning the ring stroke along
    the outline between the two stroke ends (sampled from the neighbouring
    drawn spans, so width/AA match), and a new gap of the same arc length
    is CUT into the stroke around the effective tail base. Nothing else
    moves, so an asymmetric balloon keeps its exact shape and there is
    never a double or broken line.
    """

    if effective_tail_root_param(record, tip) is None:
        return image
    do_fill = record.body_opening_params is not None  # a drawn gap to close
    do_cut = bool(record.body_open)  # open the outline at the tail base
    if not do_fill and not do_cut:
        return image
    try:
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
    except Exception:  # pragma: no cover
        return image
    from loomstroke.core.document_snapshot_builder import (  # noqa: PLC0415
        DocumentSnapshotBuilder,
    )

    params = effective_tail_base_params(record, tip) or record.tail_base_params
    qa, qb = params
    d_new = (qb - qa) % 1.0
    if d_new > 0.5:
        d_new -= 1.0
    c_new = (qa + d_new * 0.5) % 1.0
    # the cut spans the two base anchors (user-adjustable opening extent)
    half_cut = abs(d_new) * 0.5
    if do_fill:
        pa, pb = record.body_opening_params
        d_old = (pb - pa) % 1.0
        if d_old > 0.5:
            d_old -= 1.0
        c_old = (pa + d_old * 0.5) % 1.0
        half = abs(d_old) * 0.5  # the drawn gap's arc length (to fill)
        shift = (c_new - c_old + 0.5) % 1.0 - 0.5
        if do_cut and abs(shift) < 0.002 and abs(half_cut - half) < 0.002:
            return image  # opening already where/how the tail wants it
    else:
        c_old = c_new
        half = half_cut

    rgba = np.ascontiguousarray(DocumentSnapshotBuilder.qimage_to_array(image))
    h, w = rgba.shape[:2]

    # stroke width from the ink: DT of the half-coverage core (ROI only) and
    # its ridge → the drawn line width, AA-independent
    ink = ((rgba[:, :, 3] > 8).astype(np.uint8)) * 255
    if not ink.any():
        return image
    core = _core_mask(rgba[:, :, 3])
    if not core.any():
        core = ink
    dist = _distance_transform_roi(core)
    stroke_w = max(1.5, _ridge_width(dist, core, 2.0))
    cut_r = stroke_w * 1.5 + 1.0  # cut a little wider than the line

    def _arc_polyline(c: float, half_span: float, step_px: float = 2.0):
        total = outline_perimeter(record)
        count = max(2, int((2 * half_span * total) / step_px))
        return [
            outline_point_at_param(record, (c - half_span + 2 * half_span * k / (count - 1)) % 1.0)
            for k in range(count)
        ]

    # 1) CUT the new gap: erase ink within cut_r of the new-gap arc
    new_arc = _arc_polyline(c_new, half_cut)
    cut_mask = np.zeros((h, w), np.uint8)
    if do_cut:
        for (x0, y0), (x1, y1) in itertools.pairwise(new_arc):
            cv2.line(
                cut_mask, (round(x0), round(y0)), (round(x1), round(y1)), 255, round(cut_r * 2)
            )
    out = rgba.copy()
    out[cut_mask > 0] = 0
    if not do_fill:
        warped = np.ascontiguousarray(out)
        return (
            QImage(warped.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
            .copy()
            .convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        )

    # 2) FILL the old gap. Path = Hermite bridge continuing the stroke ends
    #    (curved like the balloon, not a chord). Texture = the very ink strip
    #    we just cut out at the new base, transplanted along the bridge
    #    (arc-length remap) so the drawn line quality is kept; a synthetic
    #    stroke of the estimated width backs it up where the strip is thin.
    snap_r = stroke_w * 2
    ea = _snap_to_ink(ink, outline_point_at_param(record, (c_old - half) % 1.0), snap_r, dist)
    eb = _snap_to_ink(ink, outline_point_at_param(record, (c_old + half) % 1.0), snap_r, dist)
    bridge = gap_bridge_curve(ink, ea, eb)
    ys_, xs_ = np.nonzero(ink)
    med = np.median(rgba[ys_, xs_, :3], axis=0).astype(np.uint8)
    # line width at each stroke end (2 x max inscribed radius nearby) — the
    # bridge tapers linearly from one to the other so it matches BOTH ends
    w_a = _local_stroke_width(dist, ea, snap_r, stroke_w, core)
    w_b = _local_stroke_width(dist, eb, snap_r, stroke_w, core)
    fill = np.zeros((h, w), np.uint8)
    _paint_stroke(fill, bridge, w_a, w_b)
    # the strip's own width (same estimator) → scale its across-profile so
    # the transplanted line is as thick as the ends it joins
    transplant = None
    if do_cut:
        strip_w = _region_stroke_width(dist, cut_mask & (core > 0), stroke_w)
        transplant = _transplant_strip(
            rgba, cut_mask, new_arc, bridge, cut_r, width_from=strip_w, width_to=(w_a, w_b)
        )
    # only fill where the OLD gap actually was empty (don't paint over ink):
    # synthetic stroke underneath, transplanted ink on top (its AA holes and
    # thin spots fall through to the synthetic line — no white seams)
    empty = out[:, :, 3] < 8
    fill_region = np.logical_and(fill > 0, empty)
    out[fill_region, 0] = med[0]
    out[fill_region, 1] = med[1]
    out[fill_region, 2] = med[2]
    out[fill_region, 3] = fill[fill_region]
    if transplant is not None:
        # source-over the transplanted ink onto the synthetic backing
        ta = transplant[:, :, 3].astype(np.float32) / 255.0
        ta = np.where(empty, ta, 0.0)[:, :, None]
        base_f = out.astype(np.float32)
        top_f = transplant.astype(np.float32)
        oa = base_f[:, :, 3:4] / 255.0
        out_a = ta + oa * (1.0 - ta)
        safe = np.maximum(out_a, 1e-6)
        rgb = (top_f[:, :, :3] * ta + base_f[:, :, :3] * oa * (1.0 - ta)) / safe
        blended = np.concatenate([rgb, out_a * 255.0], axis=2)
        out = np.clip(np.rint(blended), 0, 255).astype(np.uint8)
    warped = np.ascontiguousarray(out)
    return (
        QImage(warped.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
        .copy()
        .convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    )


def _auto_join_tail_ends(  # noqa: PLR0911, PLR0912, PLR0915 - one join pipeline
    body_rgba, tail_rgba, record: BalloonRecord, tip: tuple[float, float]
) -> bool:
    """Bridge the tail's stroke ends (root side) to the nearest balloon ink
    with a curved stroke whose tangents/width/colour come from the pixels
    on both sides. Draws into ``tail_rgba`` in place; True when anything
    was drawn."""

    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    corners = tail_root_corners(record, tip)
    if corners is None:
        return False
    body_ink = ((body_rgba[:, :, 3] > 8).astype(np.uint8)) * 255
    tail_ink = ((tail_rgba[:, :, 3] > 8).astype(np.uint8)) * 255
    if not body_ink.any() or not tail_ink.any():
        return False
    h, w = tail_ink.shape
    (ax, ay), (bx, by) = corners
    root_len = math.hypot(bx - ax, by - ay)
    # stroke width of the tail (for thresholds) — ridge of the core, ROI DT
    tail_core = _core_mask(tail_rgba[:, :, 3])
    if not tail_core.any():
        tail_core = tail_ink
    body_core = _core_mask(body_rgba[:, :, 3])
    if not body_core.any():
        body_core = body_ink
    tail_dist = _distance_transform_roi(tail_core)
    stroke_w = max(1.5, _ridge_width(tail_dist, tail_core, 2.0))
    band = max(24.0, root_len * 0.6, stroke_w * 6)
    # tail pixels near the root line (distance to the segment A-B)
    ys, xs = np.nonzero(tail_ink)
    px = xs.astype(np.float64)
    py = ys.astype(np.float64)
    ex, ey = bx - ax, by - ay
    seg_len2 = max(1e-9, ex * ex + ey * ey)
    tt = np.clip(((px - ax) * ex + (py - ay) * ey) / seg_len2, 0.0, 1.0)
    d_line = np.hypot(px - (ax + ex * tt), py - (ay + ey * tt))
    near = d_line <= band
    if not near.any():
        return False
    # distance from every pixel to the body ink (nearest body pixel) — only
    # needed around the tail root: ROI = tail ink bbox + reach
    reach = int(max(28.0, stroke_w * 8, root_len * 0.8, abs(record.tail_base_gap) * 1.6 + 8.0)) + 4
    roi = _roi_of(tail_ink, reach)
    body_dt = np.full(tail_ink.shape, np.inf, np.float32)
    if roi is not None:
        y0r, y1r, x0r, x1r = roi
        body_dt[y0r:y1r, x0r:x1r] = cv2.distanceTransform(
            np.ascontiguousarray(255 - body_ink[y0r:y1r, x0r:x1r]), cv2.DIST_L2, 3
        )
    # candidate end pixels: near the root AND close-ish to the body
    max_join = max(28.0, stroke_w * 8, root_len * 0.8, abs(record.tail_base_gap) * 1.6 + 8.0)
    cand = near & (body_dt[ys, xs] <= max_join)
    if not cand.any():
        return False
    cand_mask = np.zeros((h, w), np.uint8)
    cand_mask[ys[cand], xs[cand]] = 255
    count, labels = cv2.connectedComponents(cand_mask, connectivity=8)
    ends: list[tuple[float, float]] = []
    wide_end: list[bool] = []
    across_ux, across_uy = ex / math.sqrt(seg_len2), ey / math.sqrt(seg_len2)
    for label in range(1, count):
        cy_, cx_ = np.nonzero(labels == label)
        if len(cx_) < 3:
            continue
        dts = body_dt[cy_, cx_]
        closest = float(dts.min())
        sel = dts <= closest + stroke_w
        sx = cx_[sel].astype(np.float64)
        sy = cy_[sel].astype(np.float64)
        across = (sx - ax) * across_ux + (sy - ay) * across_uy
        if float(across.max() - across.min()) > 2.5 * stroke_w:
            # wide (filled) end: join both extremes
            for k in (int(np.argmin(across)), int(np.argmax(across))):
                ends.append((float(sx[k]), float(sy[k])))
                wide_end.append(True)
        else:
            k = int(np.argmin(np.hypot(sx - sx.mean(), sy - sy.mean())))
            ends.append((float(sx[k]), float(sy[k])))
            wide_end.append(False)
    if not ends:
        return False
    union = np.maximum(body_ink, tail_ink)
    ys_t, xs_t = np.nonzero(tail_ink)
    med = np.median(tail_rgba[ys_t, xs_t, :3], axis=0).astype(np.uint8)
    drawn = False
    fill = np.zeros((h, w), np.uint8)
    body_dist_in = None
    root_mx, root_my = (ax + bx) * 0.5, (ay + by) * 0.5
    for e_t, is_wide in list(zip(ends, wide_end, strict=True))[:4]:
        # nearest body ink pixel ON THE SAME SIDE of the root (left end
        # joins the left outline end, right joins right — never crossing)
        side = (e_t[0] - root_mx) * across_ux + (e_t[1] - root_my) * across_uy
        r = int(max_join) + 2
        x0, x1 = max(0, int(e_t[0]) - r), min(w, int(e_t[0]) + r + 1)
        y0, y1 = max(0, int(e_t[1]) - r), min(h, int(e_t[1]) + r + 1)
        sub_y, sub_x = np.nonzero(body_ink[y0:y1, x0:x1])
        if len(sub_x) == 0:
            continue
        sub_x = sub_x + x0
        sub_y = sub_y + y0
        b_side = (sub_x - root_mx) * across_ux + (sub_y - root_my) * across_uy
        same = (b_side * side) >= 0
        dd_all = np.hypot(sub_x - e_t[0], sub_y - e_t[1])
        if same.any() and float(dd_all[same].min()) <= max_join:
            sub_x, sub_y, dd = sub_x[same], sub_y[same], dd_all[same]
        else:
            dd = dd_all
        k = int(np.argmin(dd))
        if dd[k] < 1.5 or dd[k] > max_join:
            continue  # already touching / too far (deliberate gap)
        e_b = (float(sub_x[k]), float(sub_y[k]))
        # snap both ends onto their stroke centrelines
        e_t_c = _snap_to_ink(tail_ink, e_t, stroke_w * 1.5, tail_dist)
        if body_dist_in is None:
            body_dist_in = _distance_transform_roi(body_core, roi)
        e_b_c = _snap_to_ink(body_ink, e_b, stroke_w * 1.5, body_dist_in)
        # tangents must roughly agree with the chord (else straight): a
        # join must never hook back on itself
        bridge = gap_bridge_curve(union, e_t_c, e_b_c, min_align=0.35, tangent_scale=0.7)
        w_b = _local_stroke_width(body_dist_in, e_b_c, stroke_w * 2, stroke_w, body_core)
        # a filled tail has no "line width" at its corner: continue the
        # balloon's line width instead of tapering to a hairline
        filled_tail = is_wide or stroke_w > 1.5 * w_b
        w_t = (
            w_b
            if filled_tail
            else _local_stroke_width(tail_dist, e_t_c, stroke_w * 2, stroke_w, tail_core)
        )
        _paint_stroke(fill, bridge, w_t, w_b)
        drawn = True
    if not drawn:
        return False
    empty = tail_rgba[:, :, 3] < 8
    region = (fill > 0) & empty
    tail_rgba[region, 0] = med[0]
    tail_rgba[region, 1] = med[1]
    tail_rgba[region, 2] = med[2]
    tail_rgba[region, 3] = fill[region]
    return True


def compose_part_layer_image(  # noqa: PLR0912, PLR0915 - one compositing pipeline
    record: BalloonRecord, canvas_width: int, canvas_height: int
) -> QImage:
    """Canvas-sized image showing the balloon's raster body + tail crops.

    The balloon is opened at the tail root when ``body_open`` (cut/fill on
    the outline stroke) and the tail's stroke ends are joined to the outline
    from the pixels (``tail_auto_join``). The stored crops stay untouched.
    """

    width = int(canvas_width)
    height = int(canvas_height)
    if is_raster_rig(record):
        # raster rig balloon: the rig composes body + tail + junction
        from .raster_rig_commit import compose_rig_part_image  # noqa: PLC0415

        return compose_rig_part_image(record, width, height)

    def _blank() -> QImage:
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        return image

    body_image = _blank()
    if record.body_geometry_mode == BODY_GEOMETRY_RASTER:
        crop = decode_png_qimage(record.body_raster_png)
        if crop is not None:
            painter = QPainter(body_image)
            x, y, w, h = record.body_rect
            resized = not (abs(w - crop.width()) < 0.5 and abs(h - crop.height()) < 0.5)
            warped = None
            if resized:
                outline_local = [
                    (px * crop.width(), py * crop.height()) for px, py in record.body_outline
                ]
                warped = _edge_preserving_body_image(crop, outline_local, round(w), round(h))
            if warped is not None:
                painter.drawImage(QPoint(round(x), round(y)), warped)
            else:
                _blit(painter, crop, record.body_rect)
            painter.end()

    if (
        record.body_geometry_mode == BODY_GEOMETRY_RASTER
        and (record.body_opening_params is not None or record.body_open)
        and record.tail.enabled
    ):
        body_image = _warp_body_opening(body_image, record, record.tail.target)

    tail_image = None
    if (
        record.tail.enabled
        and record.tail_geometry_mode == TAIL_GEOMETRY_RASTER
        and record.tail_rect is not None
    ):
        crop = decode_png_qimage(record.tail_raster_png)
        if crop is not None:
            tail_image = _blank()
            painter = QPainter(tail_image)
            if not _blit_procedural_tail(painter, crop, record):
                _blit(painter, crop, record.tail_rect)
            painter.end()

    if tail_image is None:
        return body_image

    # end join where the parts meet (render-time only)
    try:
        import numpy as np  # noqa: PLC0415

        from loomstroke.core.document_snapshot_builder import (  # noqa: PLC0415
            DocumentSnapshotBuilder,
        )

        body_rgba = np.ascontiguousarray(DocumentSnapshotBuilder.qimage_to_array(body_image))
        tail_rgba = np.ascontiguousarray(DocumentSnapshotBuilder.qimage_to_array(tail_image))
        touched = False
        # ONE connection pipeline: the opening cut/fill above (when the
        # balloon is set to open) + the pixel-based end join below. The old
        # silhouette "mutual cutout" is gone — its close kernel scaled with
        # the CANVAS size and silently closed/erased small openings.
        if (
            record.tail_auto_join
            and effective_tail_root_param(record, record.tail.target) is not None
            and _auto_join_tail_ends(body_rgba, tail_rgba, record, record.tail.target)
        ):
            touched = True
        if touched:
            body_image = (
                QImage(body_rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888)
                .copy()
                .convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            )
            tail_image = (
                QImage(tail_rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888)
                .copy()
                .convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            )
    except Exception:  # pragma: no cover - compositing is best-effort
        pass

    painter = QPainter(body_image)
    painter.drawImage(QPoint(0, 0), tail_image)
    painter.end()
    return body_image


def is_raster_preserve(record: BalloonRecord) -> bool:
    return record.body_geometry_mode == BODY_GEOMETRY_RASTER or (
        record.tail_geometry_mode == TAIL_GEOMETRY_RASTER
    )


__all__ = [
    "RASTER_PART_NAMESPACE",
    "closed_outline_for_open_stroke",
    "compose_part_layer_image",
    "decode_png_qimage",
    "detect_body_opening",
    "effective_tail_base_params",
    "effective_tail_root_param",
    "encode_qimage_png",
    "ensure_root_authority",
    "ensure_tail_base_defaults",
    "find_part_layer",
    "gap_bridge_curve",
    "is_raster_preserve",
    "nearest_outline_param",
    "outline_param_facing",
    "outline_perimeter",
    "outline_point_at_param",
    "part_layer_balloon_id",
    "part_layer_marker",
    "tail_anchor_mid",
    "tail_bend_control_point",
    "tail_bend_from_handle_point",
    "tail_bend_from_point",
    "tail_bend_handle_point",
    "tail_render_transform",
    "tail_root_corners",
    "tail_root_half_px",
    "tail_root_local_adjust",
    "tail_root_on_body",
    "tight_alpha_crop",
]
