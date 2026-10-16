"""Body Attachment Guide (hidden control outline) — BZ4R spike."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..raster_parts import _core_mask, _distance_transform_roi, _ridge_width

# an outline that does not close encloses only a stroke-thin sliver: its
# inscribed radius over the short bbox side stays far below this (see
# build_attachment_guide)
OPEN_OUTLINE_INSCRIBED_RATIO = 0.12


@dataclass
class AttachmentGuide:
    """Hidden control outline of a body image.

    Everything is in BODY-LOCAL pixel coordinates (image origin); the page
    placement is applied by the caller via ``origin``.
    """

    origin: tuple[float, float]
    outline: np.ndarray  # (N, 2) smoothed control outline, closed (last != first)
    cum: np.ndarray  # (N,) cumulative arc length at each vertex
    perimeter: float
    tangents: np.ndarray  # (N, 2) unit
    normals: np.ndarray  # (N, 2) unit, OUTWARD
    holes: list[list[tuple[float, float]]] = field(default_factory=list)
    decorations: list[tuple[int, int, int, int]] = field(default_factory=list)  # bboxes
    allowed: list[tuple[float, float]] = field(default_factory=lambda: [(0.0, 1.0)])
    forbidden: list[tuple[float, float]] = field(default_factory=list)
    line_width: float = 2.0
    fill_ratio: float = 0.0
    smoothing_px: float = 0.0
    main_bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    #: per-vertex excursion (px) of the RAW outline from the smoothed guide:
    #: ~line width/2 on a clean curve, spike/bump amplitude on decorated ones
    excursion: np.ndarray | None = None

    # ── geometry queries ───────────────────────────────────────────────
    def _locate(self, u: float) -> tuple[int, float]:
        target = (u % 1.0) * self.perimeter
        idx = int(np.searchsorted(self.cum, target, side="right") - 1)
        idx = max(0, min(len(self.outline) - 1, idx))
        nxt = (idx + 1) % len(self.outline)
        seg = float(self.cum[nxt] - self.cum[idx]) if nxt else float(self.perimeter - self.cum[idx])
        frac = 0.0 if seg <= 1e-9 else (target - float(self.cum[idx])) / seg
        return idx, max(0.0, min(1.0, frac))

    def point_at(self, u: float) -> tuple[float, float]:
        idx, frac = self._locate(u)
        a = self.outline[idx]
        b = self.outline[(idx + 1) % len(self.outline)]
        return (float(a[0] + (b[0] - a[0]) * frac), float(a[1] + (b[1] - a[1]) * frac))

    def frame_at(
        self, u: float
    ) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
        """(point, unit tangent, unit outward normal) at ``u``."""

        idx, frac = self._locate(u)
        nxt = (idx + 1) % len(self.outline)
        t = self.tangents[idx] * (1 - frac) + self.tangents[nxt] * frac
        n = self.normals[idx] * (1 - frac) + self.normals[nxt] * frac
        tl = float(np.hypot(*t)) or 1.0
        nl = float(np.hypot(*n)) or 1.0
        return (
            self.point_at(u),
            (float(t[0] / tl), float(t[1] / tl)),
            (float(n[0] / nl), float(n[1] / nl)),
        )

    def nearest_u(
        self, point: tuple[float, float], *, u0: float | None = None, window: float | None = None
    ) -> float:
        """u of the closest outline point; with ``u0``/``window`` the search
        is restricted to ``u0 ± window`` (continuous tracking)."""

        pts = self.outline
        nxt = np.roll(pts, -1, axis=0)
        e = nxt - pts
        seg2 = np.maximum(1e-9, (e * e).sum(axis=1))
        v = np.asarray(point, dtype=np.float64) - pts
        s = np.clip((v * e).sum(axis=1) / seg2, 0.0, 1.0)
        foot = pts + e * s[:, None]
        d = np.hypot(*(np.asarray(point) - foot).T)
        seg_len = np.sqrt(seg2)
        u_all = (self.cum + seg_len * s) / max(1e-9, self.perimeter)
        if u0 is not None and window is not None:
            du = np.abs((u_all - u0 + 0.5) % 1.0 - 0.5)
            d = np.where(du <= window, d, np.inf)
            if not np.isfinite(d).any():
                return float(u0)
        return float(u_all[int(np.argmin(d))] % 1.0)

    def is_allowed(self, u: float) -> bool:
        u %= 1.0
        for a, b in self.forbidden:
            if _in_interval(u, a, b):
                return False
        return any(_in_interval(u, a, b) for a, b in self.allowed)

    def clamp_allowed(self, u: float) -> float:
        if self.is_allowed(u):
            return u % 1.0
        # nearest allowed boundary
        best, best_d = u % 1.0, float("inf")
        for a, b in self.allowed:
            for edge in (a, b):
                d = abs((edge - u + 0.5) % 1.0 - 0.5)
                if d < best_d and self.is_allowed(edge + (1e-4 if edge == a else -1e-4)):
                    best, best_d = edge % 1.0, d
        return best

    def excursion_over(self, u0: float, u1: float) -> float:
        """Max raw-outline excursion (px) from the guide over the arc
        [u0, u1] (short way round)."""

        if self.excursion is None or len(self.excursion) == 0:
            return 0.0
        n = len(self.outline)
        i0, _ = self._locate(u0)
        i1, _ = self._locate(u1)
        idx = list(range(i0, i1 + 1)) if i0 <= i1 else list(range(i0, n)) + list(range(0, i1 + 1))
        return float(np.max(self.excursion[idx])) if idx else 0.0

    def curvature_at(self, u: float, ds_px: float | None = None) -> float:
        ds = ds_px or max(2.0, self.perimeter * 0.01)
        du = ds / max(1e-9, self.perimeter)
        _, t0, _ = self.frame_at(u - du)
        _, t1, _ = self.frame_at(u + du)
        ang = math.atan2(t0[0] * t1[1] - t0[1] * t1[0], t0[0] * t1[0] + t0[1] * t1[1])
        return ang / (2 * ds)

    # ── serialisation ─────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": [float(v) for v in self.origin],
            "outline": [[float(x), float(y)] for x, y in self.outline],
            "holes": [[[round(x, 1), round(y, 1)] for x, y in h] for h in self.holes],
            "decorations": [list(map(int, b)) for b in self.decorations],
            "allowed": [[float(a), float(b)] for a, b in self.allowed],
            "forbidden": [[float(a), float(b)] for a, b in self.forbidden],
            "line_width": float(self.line_width),
            "fill_ratio": float(self.fill_ratio),
            "smoothing_px": float(self.smoothing_px),
            "main_bbox": list(map(int, self.main_bbox)),
            "excursion": (
                [float(v) for v in self.excursion] if self.excursion is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttachmentGuide:
        outline = np.asarray(data["outline"], dtype=np.float64)
        guide = _finish_guide(
            tuple(data["origin"]),
            outline,
            holes=[[tuple(p) for p in h] for h in data.get("holes", [])],
            decorations=[tuple(b) for b in data.get("decorations", [])],
            line_width=float(data.get("line_width", 2.0)),
            fill_ratio=float(data.get("fill_ratio", 0.0)),
            smoothing_px=float(data.get("smoothing_px", 0.0)),
            main_bbox=tuple(data.get("main_bbox", (0, 0, 0, 0))),
        )
        guide.allowed = [tuple(v) for v in data.get("allowed", [[0.0, 1.0]])]
        guide.forbidden = [tuple(v) for v in data.get("forbidden", [])]
        exc = data.get("excursion")
        if exc is not None:
            values = np.asarray(exc, dtype=np.float64)
            guide.excursion = _reorder_like(values, outline, guide.outline)
        return guide


def _reorder_like(values: np.ndarray, before: np.ndarray, after: np.ndarray) -> np.ndarray:
    """``_finish_guide`` may reverse the vertex order; keep per-vertex
    values aligned with the stored outline."""

    if len(before) == len(after) and not np.allclose(before[:3], after[:3]):
        return values[::-1].copy()
    return values


def _in_interval(u: float, a: float, b: float) -> bool:
    a %= 1.0
    b %= 1.0
    if a <= b:
        return a <= u <= b
    return u >= a or u <= b


def _finish_guide(origin, outline: np.ndarray, **kw) -> AttachmentGuide:
    """Compute cum/tangent/normal for a closed outline (CCW enforced so the
    +90° rotation of the tangent points OUTWARD in image coordinates)."""

    pts = np.asarray(outline, dtype=np.float64)
    if len(pts) < 3:
        raise ValueError("outline too short")
    # signed area (image coords: y down) — make the polygon go clockwise on
    # screen, i.e. positive area with the y-down convention, so that the
    # right-hand normal (t rotated by -90°) points outward
    x, y = pts[:, 0], pts[:, 1]
    area = 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    if area < 0:
        pts = pts[::-1].copy()
    nxt = np.roll(pts, -1, axis=0)
    seg = np.hypot(*(nxt - pts).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)[:-1]])
    perimeter = float(seg.sum())
    prv = np.roll(pts, 1, axis=0)
    t = nxt - prv
    tl = np.maximum(1e-9, np.hypot(*t.T))
    t = t / tl[:, None]
    # for positive (screen-clockwise) area, outward = (t.y, -t.x)
    n = np.stack([t[:, 1], -t[:, 0]], axis=1)
    # verify orientation numerically: normal at a vertex should point away
    # from the polygon centroid on average
    c = pts.mean(axis=0)
    if float(np.mean(((pts - c) * n).sum(axis=1))) < 0:
        n = -n
    return AttachmentGuide(
        origin=(float(origin[0]), float(origin[1])),
        outline=pts,
        cum=cum,
        perimeter=perimeter,
        tangents=t,
        normals=n,
        **kw,
    )


def _resample_closed(points: np.ndarray, step_px: float) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    nxt = np.roll(pts, -1, axis=0)
    seg = np.hypot(*(nxt - pts).T)
    total = float(seg.sum())
    if total <= 0:
        return pts
    count = max(24, int(total / max(0.5, step_px)))
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    targets = np.linspace(0.0, total, count, endpoint=False)
    idx = np.clip(np.searchsorted(cum, targets, side="right") - 1, 0, len(pts) - 1)
    frac = (targets - cum[idx]) / np.maximum(1e-9, seg[idx])
    return pts[idx] + (nxt[idx] - pts[idx]) * frac[:, None]


def _smooth_closed(points: np.ndarray, sigma_samples: float) -> np.ndarray:
    if sigma_samples <= 0.3:
        return points
    radius = math.ceil(sigma_samples * 3)
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma_samples) ** 2)
    k /= k.sum()
    out = np.empty_like(points)
    for axis in range(2):
        col = points[:, axis]
        padded = np.concatenate([col[-radius:], col, col[:radius]])
        out[:, axis] = np.convolve(padded, k, mode="valid")
    return out


def build_attachment_guide(  # noqa: PLR0912, PLR0915 - one extraction pipeline
    body_rgba: np.ndarray,
    origin: tuple[float, float] = (0.0, 0.0),
    *,
    smoothing_px: float | None = None,
) -> AttachmentGuide:
    """Extract the hidden Attachment Guide from a body RGBA image.

    * main perimeter = external contour of the largest ink component after
      a closing of ~2x the detected line width (bridges brush gaps); the
      display pixels are never touched — only this hidden guide is smoothed
    * holes = inner contours of that component (outline balloons have one
      big hole: the text area)
    * decorations = every other component (splatter, dots) — kept with the
      image, excluded from the guide
    """

    import cv2  # noqa: PLC0415

    alpha = body_rgba[:, :, 3]
    ink = ((alpha > 8).astype(np.uint8)) * 255
    if not ink.any():
        raise ValueError("body has no ink")
    core = _core_mask(alpha)
    if not core.any():
        core = ink
    dist = _distance_transform_roi(core)
    line_width = max(1.5, _ridge_width(dist, core, 2.0))
    # consolidate broken/brushy outlines before picking the main component.
    # The kernel is capped: for a FILLED body the "line width" is the whole
    # shape and an uncapped closing would swallow nearby decorations
    k = int(max(3, min(9, round(line_width * 2))) | 1)
    consolidated = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    count, labels, stats, _c = cv2.connectedComponentsWithStats(consolidated, connectivity=8)
    if count <= 1:
        raise ValueError("no components")
    areas = stats[1:, cv2.CC_STAT_AREA]
    main_label = int(np.argmax(areas)) + 1
    main_mask = ((labels == main_label).astype(np.uint8)) * 255
    decorations: list[tuple[int, int, int, int]] = []
    for label in range(1, count):
        if label == main_label:
            continue
        x, y, w, h, _a = stats[label]
        decorations.append((int(x), int(y), int(w), int(h)))
    contours, hierarchy = cv2.findContours(main_mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise ValueError("no contour")
    hier = hierarchy[0] if hierarchy is not None else None
    outer_idx = None
    outer_area = -1.0
    holes: list[list[tuple[float, float]]] = []
    for i, cnt in enumerate(contours):
        parent = hier[i][3] if hier is not None else -1
        if parent == -1:
            a = float(cv2.contourArea(cnt))
            if a > outer_area:
                outer_area = a
                outer_idx = i
    if outer_idx is None:
        raise ValueError("no outer contour")
    # an OPEN outline (hand-drawn body with a gap the capped closing cannot
    # seal): the external contour then hugs the stroke itself — out along
    # one side, around the end, back along the other — and "encloses" no
    # interior at all. Every downstream step (root on the perimeter,
    # interior clip/fill, holes) assumes a closed perimeter, so reject with
    # a reason instead of registering a degenerate guide (QA-4, 2026-08-23).
    # Test: the largest inscribed circle of the enclosed region is a
    # stroke-sized sliver compared with the body's own extent (measured:
    # open strokes 0.02-0.08 of the bbox's short side even at 30 px lines;
    # every closed/filled body and real fixture 0.32-0.49).
    enclosed = np.zeros(ink.shape, np.uint8)
    cv2.fillPoly(enclosed, [contours[outer_idx]], 255)
    inscribed = float(cv2.distanceTransform(enclosed, cv2.DIST_L2, 3).max())
    _bx, _by, bw, bh = cv2.boundingRect(contours[outer_idx])
    if inscribed < OPEN_OUTLINE_INSCRIBED_RATIO * max(1, min(bw, bh)):
        raise ValueError("outline is not closed (gap in the body outline)")
    for i, cnt in enumerate(contours):
        parent = hier[i][3] if hier is not None else -1
        if parent == outer_idx and float(cv2.contourArea(cnt)) > 4 * line_width * line_width:
            approx = cv2.approxPolyDP(cnt, 1.5, True).reshape(-1, 2)
            holes.append([(float(x), float(y)) for x, y in approx])
    outer = contours[outer_idx].reshape(-1, 2).astype(np.float64)
    perimeter = float(cv2.arcLength(contours[outer_idx], True))
    ink_area = float(np.count_nonzero(ink & main_mask))
    fill_ratio = ink_area / max(1.0, outer_area)
    if smoothing_px is None:
        smoothing_px = float(min(12.0, max(2.0, perimeter * 0.006)))
    step = max(1.0, min(3.0, perimeter / 400.0))
    resampled = _resample_closed(outer, step)
    control = _smooth_closed(resampled, smoothing_px / step)
    # excursion of the raw outline from the guide, plus the ink's own half
    # width (the contour is the OUTER edge of the stroke)
    exc = np.hypot(*(resampled - control).T) + line_width * 0.5
    x, y, w, h = cv2.boundingRect(contours[outer_idx])
    guide = _finish_guide(
        origin,
        control,
        holes=holes,
        decorations=decorations,
        line_width=float(line_width),
        fill_ratio=float(fill_ratio),
        smoothing_px=float(smoothing_px),
        main_bbox=(int(x), int(y), int(w), int(h)),
    )
    guide.excursion = _reorder_like(exc, control, guide.outline)
    return guide


__all__ = ["AttachmentGuide", "build_attachment_guide"]
