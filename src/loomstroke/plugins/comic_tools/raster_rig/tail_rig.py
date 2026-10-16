"""Tail internal rig + non-destructive deformer — BZ4R spike."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..raster_parts import _core_mask, _distance_transform_roi, _ridge_width
from .guide import AttachmentGuide

DEFORM_KINDS = ("rigid", "smooth", "corner_preserve", "skeleton")


@dataclass
class TailRig:
    """Hidden rig of one tail image (TAIL-LOCAL pixel coordinates)."""

    origin: tuple[float, float]  # registered page placement of the tail image
    size: tuple[int, int]  # (w, h)
    base_left: tuple[float, float]
    base_right: tuple[float, float]
    base_centre: tuple[float, float]
    tip: tuple[float, float]
    spine: np.ndarray  # (M, 2) rest spine from base_centre to tip
    deform_kind: str = "smooth"
    rigid_fraction: float = 0.2  # rest-arc fraction from the base kept rigid
    line_width: float = 2.0
    base_width: float = 0.0
    contact_u: float = 0.0  # guide u the drawn base faced at registration
    contact_gap: float = 0.0  # px between drawn base centre and the guide point
    filled: bool = False
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)  # ink bbox (x, y, w, h)
    notes: list[str] = field(default_factory=list)
    # non-destructive registration scale: source px -> rig px (uniform).
    # The geometry above is ALWAYS in SOURCE pixels (what the detector
    # measured); ``rig_space()`` derives the scaled rig on demand. The
    # scale is anchored at ``base_centre`` so the contact point stays put.
    source_to_rig_scale: float = 1.0

    # ── scale ─────────────────────────────────────────────────────────
    @staticmethod
    def validate_scale(scale: float) -> float:
        s = float(scale)
        if not math.isfinite(s) or s <= 0.0:
            raise ValueError(f"source_to_rig_scale must be a positive finite number, got {scale!r}")
        return s

    def rig_space(self) -> TailRig:
        """The rig in RIG pixels (source geometry x scale, anchored at the
        base centre: page = origin + base_centre + (p - base_centre) * s).
        Returns ``self`` untouched when the scale is exactly 1.0 so the
        unscaled path stays bit-identical."""

        s = self.source_to_rig_scale
        if s == 1.0:
            return self
        s = self.validate_scale(s)
        bx, by = self.base_centre

        def pt(p: tuple[float, float]) -> tuple[float, float]:
            return (float(p[0]) * s, float(p[1]) * s)

        x0, y0, w, h = self.bbox
        return TailRig(
            origin=(self.origin[0] + bx * (1.0 - s), self.origin[1] + by * (1.0 - s)),
            size=(math.ceil(self.size[0] * s), math.ceil(self.size[1] * s)),
            base_left=pt(self.base_left),
            base_right=pt(self.base_right),
            base_centre=pt(self.base_centre),
            tip=pt(self.tip),
            spine=np.asarray(self.spine, dtype=np.float64) * s,
            deform_kind=self.deform_kind,
            rigid_fraction=self.rigid_fraction,
            line_width=self.line_width * s,
            base_width=self.base_width * s,
            contact_u=self.contact_u,
            contact_gap=self.contact_gap,  # anchored at the base centre: unchanged
            filled=self.filled,
            bbox=(
                math.floor(x0 * s),
                math.floor(y0 * s),
                math.ceil((x0 + w) * s) - math.floor(x0 * s),
                math.ceil((y0 + h) * s) - math.floor(y0 * s),
            ),
            notes=list(self.notes),
            source_to_rig_scale=1.0,
        )

    # ── derived ───────────────────────────────────────────────────────
    @property
    def spine_length(self) -> float:
        d = np.diff(self.spine, axis=0)
        return float(np.hypot(d[:, 0], d[:, 1]).sum())

    @property
    def rest_axis(self) -> tuple[tuple[float, float], float]:
        ax = self.tip[0] - self.base_centre[0]
        ay = self.tip[1] - self.base_centre[1]
        length = math.hypot(ax, ay)
        if length < 1e-9:
            return (0.0, 1.0), 0.0
        return (ax / length, ay / length), length

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": [float(v) for v in self.origin],
            "size": [int(v) for v in self.size],
            "base_left": [float(v) for v in self.base_left],
            "base_right": [float(v) for v in self.base_right],
            "base_centre": [float(v) for v in self.base_centre],
            "tip": [float(v) for v in self.tip],
            "spine": [[float(x), float(y)] for x, y in self.spine],
            "deform_kind": self.deform_kind,
            "rigid_fraction": float(self.rigid_fraction),
            "line_width": float(self.line_width),
            "base_width": float(self.base_width),
            "contact_u": float(self.contact_u),
            "contact_gap": float(self.contact_gap),
            "filled": bool(self.filled),
            "bbox": [int(v) for v in self.bbox],
            "notes": list(self.notes),
            "source_to_rig_scale": float(self.source_to_rig_scale),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TailRig:
        return cls(
            origin=tuple(data["origin"]),
            size=tuple(data["size"]),
            base_left=tuple(data["base_left"]),
            base_right=tuple(data["base_right"]),
            base_centre=tuple(data["base_centre"]),
            tip=tuple(data["tip"]),
            spine=np.asarray(data["spine"], dtype=np.float64),
            deform_kind=str(data.get("deform_kind", "smooth")),
            rigid_fraction=float(data.get("rigid_fraction", 0.2)),
            line_width=float(data.get("line_width", 2.0)),
            base_width=float(data.get("base_width", 0.0)),
            contact_u=float(data.get("contact_u", 0.0)),
            contact_gap=float(data.get("contact_gap", 0.0)),
            filled=bool(data.get("filled", False)),
            bbox=tuple(data.get("bbox", (0, 0, 0, 0))),
            notes=list(data.get("notes", [])),
            source_to_rig_scale=cls.validate_scale(data.get("source_to_rig_scale", 1.0)),
        )


# ── skeleton path (medial axis walk from base to tip) ──────────────────


def _medial_path(fill_mask: np.ndarray, start: tuple[int, int], goal: tuple[int, int]):
    """Cheapest 8-connected path inside ``fill_mask`` from ``start`` to
    ``goal`` where cost favours the medial axis (large distance-transform
    values). Returns a list of (x, y) or None."""

    import cv2  # noqa: PLC0415

    h, w = fill_mask.shape
    dist = cv2.distanceTransform(fill_mask, cv2.DIST_L2, 3)
    cost = 1.0 / (0.5 + dist)  # cheap on the ridge, expensive near the edge
    sx, sy = start
    gx, gy = goal
    if not (0 <= sx < w and 0 <= sy < h and 0 <= gx < w and 0 <= gy < h):
        return None
    best = np.full((h, w), np.inf, np.float64)
    prev = np.full((h, w, 2), -1, np.int32)
    best[sy, sx] = 0.0
    heap = [(0.0, sx, sy)]
    while heap:
        c, x, y = heapq.heappop(heap)
        if (x, y) == (gx, gy):
            break
        if c > best[y, x]:
            continue
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if not (0 <= nx < w and 0 <= ny < h) or fill_mask[ny, nx] == 0:
                    continue
                step = math.hypot(dx, dy) * cost[ny, nx]
                nc = c + step
                if nc < best[ny, nx]:
                    best[ny, nx] = nc
                    prev[ny, nx] = (x, y)
                    heapq.heappush(heap, (nc, nx, ny))
    if not np.isfinite(best[gy, gx]):
        return None
    path = []
    x, y = gx, gy
    while (x, y) != (sx, sy):
        path.append((x, y))
        px, py = prev[y, x]
        if px < 0:
            return None
        x, y = int(px), int(py)
    path.append((sx, sy))
    path.reverse()
    return path


def _simplify(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    import cv2  # noqa: PLC0415

    if len(points) < 3:
        return list(points)
    arr = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
    out = cv2.approxPolyDP(arr, float(eps), False).reshape(-1, 2)
    return [(float(x), float(y)) for x, y in out]


def _corner_indices(spine: np.ndarray, angle_deg: float, min_leg: float = 0.0) -> list[int]:
    """Indices of genuine corners: turn >= ``angle_deg`` AND both legs at
    least ``min_leg`` long (short wiggles of the medial walk between two
    drawn lines are not corners)."""

    idx = []
    for i in range(1, len(spine) - 1):
        a = spine[i] - spine[i - 1]
        b = spine[i + 1] - spine[i]
        la, lb = np.hypot(*a), np.hypot(*b)
        if la < max(1e-6, min_leg) or lb < max(1e-6, min_leg):
            continue
        cosang = float(np.clip((a @ b) / (la * lb), -1.0, 1.0))
        if math.degrees(math.acos(cosang)) >= angle_deg:
            idx.append(i)
    return idx


def build_tail_rig(  # noqa: PLR0912, PLR0915 - one extraction pipeline
    tail_rgba: np.ndarray,
    tail_origin: tuple[float, float],
    guide: AttachmentGuide,
    *,
    deform_kind: str | None = None,
) -> TailRig:
    """Derive the tail rig from the tail image and its registered placement
    relative to the body guide.

    * base = the ink nearest the body outline (band of ~2 line widths);
      base left/right = its extremes along the outline tangent
    * tip = ink pixel farthest from the base line
    * spine = medial-axis walk (Dijkstra on the distance transform of the
      consolidated tail silhouette) from base centre to tip, simplified
    * deform kind (initial proposal only): corner_preserve when the spine
      has sharp corners, skeleton when it is clearly curved, else smooth
    """

    import cv2  # noqa: PLC0415

    alpha = tail_rgba[:, :, 3]
    h, w = alpha.shape
    ink = ((alpha > 8).astype(np.uint8)) * 255
    if not ink.any():
        raise ValueError("tail has no ink")
    core = _core_mask(alpha)
    if not core.any():
        core = ink
    dist = _distance_transform_roi(core)
    line_width = max(1.5, _ridge_width(dist, core, 2.0))
    notes: list[str] = []

    # keep every component that is near the largest one (two-line tails are
    # two components); far pieces are flagged
    count, labels, stats, _c = cv2.connectedComponentsWithStats(ink, connectivity=8)
    areas = stats[1:, cv2.CC_STAT_AREA]
    main = int(np.argmax(areas)) + 1
    mx, my, mw, mh, _ = stats[main]
    keep = np.zeros_like(ink)
    reach = max(6.0, line_width * 6)
    for label in range(1, count):
        x, y, cw, ch, a = stats[label]
        near = not (
            x > mx + mw + reach or x + cw < mx - reach or y > my + mh + reach or y + ch < my - reach
        )
        if label == main or near:
            keep[labels == label] = 255
        else:
            notes.append(f"far component ignored (area {int(a)})")
    ink = keep
    ys, xs = np.nonzero(ink)
    px = xs.astype(np.float64) + tail_origin[0] - guide.origin[0]  # body-local
    py = ys.astype(np.float64) + tail_origin[1] - guide.origin[1]

    # which END of the tail is the base? Use the tail's own principal axis
    # (PCA of its ink) and pick the end whose pixels are nearer the body's
    # main-outline centroid - robust against neighbouring spikes/bumps of
    # the body brushing the tail's flanks
    cx0, cy0 = float(px.mean()), float(py.mean())
    cov = np.cov(np.stack([px - cx0, py - cy0]))
    vals, vecs = np.linalg.eigh(cov)
    ax_u = vecs[:, -1]
    if abs(float(vals[-1])) < 1e-9:
        ax_u = np.array([0.0, 1.0])
    proj = (px - cx0) * ax_u[0] + (py - cy0) * ax_u[1]
    lo, hi = float(proj.min()), float(proj.max())
    span = max(1e-6, hi - lo)
    gx_ = guide.main_bbox[0] + guide.main_bbox[2] / 2
    gy_ = guide.main_bbox[1] + guide.main_bbox[3] / 2
    end_lo = proj <= lo + span * 0.15
    end_hi = proj >= hi - span * 0.15
    d_lo = float(np.hypot(px[end_lo].mean() - gx_, py[end_lo].mean() - gy_))
    d_hi = float(np.hypot(px[end_hi].mean() - gx_, py[end_hi].mean() - gy_))
    if d_hi < d_lo:
        ax_u = -ax_u
        proj = -proj
        lo, hi = -hi, -lo
    # base band = ink in the first ~15% of the axis (at least 2 line widths)
    band_len = max(span * 0.15, line_width * 2.0)
    band = proj <= lo + band_len
    if band.sum() < 3:
        raise ValueError("tail base not found")
    # base left/right = extremes of the band ACROSS the axis
    across = -(px - cx0) * ax_u[1] + (py - cy0) * ax_u[0]
    bxs, bys = xs[band], ys[band]
    i_left = int(np.argmin(across[band]))
    i_right = int(np.argmax(across[band]))
    base_left = (float(bxs[i_left]), float(bys[i_left]))
    base_right = (float(bxs[i_right]), float(bys[i_right]))
    base_centre = ((base_left[0] + base_right[0]) * 0.5, (base_left[1] + base_right[1]) * 0.5)
    base_width = math.hypot(base_right[0] - base_left[0], base_right[1] - base_left[1])
    # contact: nearest guide point to the base centre (body-local)
    bc_body = (
        base_centre[0] + tail_origin[0] - guide.origin[0],
        base_centre[1] + tail_origin[1] - guide.origin[1],
    )
    contact_u = guide.nearest_u(bc_body)
    cp = guide.point_at(contact_u)
    contact_gap = float(math.hypot(bc_body[0] - cp[0], bc_body[1] - cp[1]))
    # multi-attach: ink close to the outline in places far apart along u
    d2 = (px[:, None] - guide.outline[None, :, 0]) ** 2 + (
        py[:, None] - guide.outline[None, :, 1]
    ) ** 2
    d_body = np.sqrt(d2.min(axis=1))
    near_body = d_body <= max(3.0, line_width * 1.5)
    if near_body.sum() >= 3:
        nb_mask = np.zeros_like(ink)
        nb_mask[ys[near_body], xs[near_body]] = 255
        n_touch, touch_labels = cv2.connectedComponents(nb_mask, connectivity=8)
        touch_us = []
        for label in range(1, n_touch):
            ty_, tx_ = np.nonzero(touch_labels == label)
            if len(tx_) < 3:
                continue
            cx_ = float(tx_.mean()) + tail_origin[0] - guide.origin[0]
            cy_ = float(ty_.mean()) + tail_origin[1] - guide.origin[1]
            touch_us.append(guide.nearest_u((cx_, cy_)))
        if len(touch_us) >= 2:
            span_u = 0.0
            for a in touch_us:
                for b in touch_us:
                    span_u = max(span_u, abs((a - b + 0.5) % 1.0 - 0.5))
            if span_u > 0.25:
                notes.append(
                    f"base touches the body in {len(touch_us)} places "
                    f"spanning {span_u:.2f} of the outline"
                )

    # tip: farthest ink pixel along the tail axis from the base
    i_tip = int(np.argmax(proj))
    tip = (float(xs[i_tip]), float(ys[i_tip]))

    # silhouette fill for the medial walk (closes two-line tails)
    # the closing must bridge the full gap between two drawn lines (they are
    # at most base_width apart) or the medial walk zigzags between them
    kk = int(max(3, round(base_width * 1.2)) | 1)
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((kk, kk), np.uint8))
    contours, _hh = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    fill = np.zeros_like(ink)
    if contours:
        cv2.fillPoly(fill, [max(contours, key=cv2.contourArea)], 255)
    fill = cv2.bitwise_or(fill, ink)
    filled = float(np.count_nonzero(ink)) / max(1.0, float(np.count_nonzero(fill))) > 0.6

    start = (round(base_centre[0]), round(base_centre[1]))
    goal = (round(tip[0]), round(tip[1]))
    # snap start/goal into the fill
    fy, fx = np.nonzero(fill)
    if fill[start[1], start[0]] == 0:
        k = int(np.argmin((fx - start[0]) ** 2 + (fy - start[1]) ** 2))
        start = (int(fx[k]), int(fy[k]))
    if fill[goal[1], goal[0]] == 0:
        k = int(np.argmin((fx - goal[0]) ** 2 + (fy - goal[1]) ** 2))
        goal = (int(fx[k]), int(fy[k]))
    path = _medial_path(fill, start, goal)
    if path is None or len(path) < 2:
        spine_pts = [base_centre, tip]
        notes.append("medial path failed; straight spine")
    else:
        spine_pts = _simplify([(float(x), float(y)) for x, y in path], max(1.5, line_width))
        spine_pts[0] = base_centre
        spine_pts[-1] = tip
    spine = np.asarray(spine_pts, dtype=np.float64)

    # deform kind proposal
    d_all = np.diff(spine, axis=0)
    spine_len_px = float(np.hypot(d_all[:, 0], d_all[:, 1]).sum()) if len(spine) > 1 else 0.0
    min_leg = max(3.0 * line_width, 0.12 * spine_len_px)
    if deform_kind is None:
        corners = _corner_indices(spine, 35.0, min_leg)
        d = np.diff(spine, axis=0)
        arc = float(np.hypot(d[:, 0], d[:, 1]).sum())
        chord = math.hypot(tip[0] - base_centre[0], tip[1] - base_centre[1])
        if corners:
            deform_kind = "corner_preserve"
        elif chord > 1e-6 and arc / chord > 1.12:
            deform_kind = "skeleton"
        else:
            deform_kind = "smooth"
    if deform_kind == "corner_preserve":
        corners = _corner_indices(spine, 35.0, min_leg)
        spine = np.asarray([spine[0]] + [spine[i] for i in corners] + [spine[-1]], dtype=np.float64)
    elif deform_kind in ("smooth", "rigid"):
        spine = np.asarray([base_centre, tip], dtype=np.float64)

    x0, y0, bw_, bh_ = cv2.boundingRect(np.stack([xs, ys], axis=1).astype(np.int32))
    return TailRig(
        origin=(float(tail_origin[0]), float(tail_origin[1])),
        size=(int(w), int(h)),
        base_left=base_left,
        base_right=base_right,
        base_centre=base_centre,
        tip=tip,
        spine=spine,
        deform_kind=deform_kind,
        rigid_fraction=0.2,
        line_width=float(line_width),
        base_width=float(base_width),
        contact_u=contact_u,
        contact_gap=float(contact_gap),
        filled=bool(filled),
        bbox=(int(x0), int(y0), int(bw_), int(bh_)),
        notes=notes,
    )


__all__ = ["DEFORM_KINDS", "TailRig", "build_tail_rig"]
