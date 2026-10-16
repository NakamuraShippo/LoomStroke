"""Opening / junction compositing for the raster balloon rig.

Production home of what the BZ4R harness prototyped as ``open_ink`` and
``open_ink_fill`` (previously ``tests/support/raster_rig_open_ink.py``,
which now re-exports from here):

* :func:`stroke_mask` — data-driven stroke/fill analysis of the body
  (luminance histogram + outer rim; no style branches);
* :class:`InkOpeningComposer` — ``open_ink``: the opening band only cuts
  the body's STROKE pixels that the posed tail does not cover, the arc is
  derived from the POSED tail legs (hard ends at the legs' outer corners,
  and a pixel the tail ink touches is never cut, so the outline beyond a
  leg stays and the outline under a leg stays solid — leg and outline
  fuse), the band depth follows the measured outline-stroke width (not
  the filled shape's width), tail ink falling INSIDE the body interior is
  clipped (the legs stop at the outline's inner edge), and the cut outline
  ends are bridged to the leg endpoints;
* :class:`InkFillOpeningComposer` — ``open_ink_fill``: the opened stroke
  pixels are repainted with the body's fill colour (light-filled bodies),
  the closed tail interior is painted with it, and
  ``junction_fill_inset_px`` sweeps the junction fill INTO the body along
  the inward outline normal (directional, ROI-clipped, never over a living
  stroke; 0 = no extension).

Everything is recomputed from the pristine sources every frame; the only
caches are derived maps (stroke map, tail interior, band mask keyed by its
exact inputs) that are never persisted. Interactive/settle preview quality
differs only in band sampling density (``RigComposer.preview_quality``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

from ..raster_parts import _distance_transform_roi, _ridge_width
from .compose import (
    MaskPatch,
    RigComposer,
    TailPatch,
    TailPose,
    _clip_rect,
    _over,
    _rect_union,
)
from .recipe import CompositingRecipe

LIGHT_FILL_LUM = 128
STROKE_DELTA = 64
JUNCTION_INSET_MAX_LW = 4.0  # safe cap: inset <= this x body line width
INTERIOR_CLOSE_MAX_K = 9  # outline consolidation kernel cap (as the guide builder)
DARK_FILL_MIN_RATIO = 0.5  # ink_fallback body at least this filled = a dark FILLED body


# ── stroke / fill analysis ────────────────────────────────────────────


def stroke_mask(body_rgba: np.ndarray) -> tuple[np.ndarray, dict]:
    """(h, w) float32 0..1 stroke weight in body-local coords + info.

    Opaque pixels; dominant opaque luminance L0 = the fill. Light L0
    (> 128): stroke = ``lum < L0 - 64`` (soft) plus the 2 px outer rim
    (binary-alpha exports carry the stroke's light AA fringe as opaque
    light-grey pixels). Dark L0: every opaque pixel is stroke
    (``ink_fallback`` — line bodies and dark-filled bodies)."""

    import cv2  # noqa: PLC0415

    a = body_rgba[:, :, 3].astype(np.float32)
    opaque = a > 8
    rgb = body_rgba[:, :, :3].astype(np.float32)
    lum = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    if not opaque.any():
        return np.zeros(a.shape, np.float32), {"mode": "empty"}
    hist = np.bincount(lum[opaque].astype(np.int32), minlength=256)
    win = np.convolve(hist, np.ones(9), mode="same")
    l0 = int(np.argmax(win))
    if l0 > LIGHT_FILL_LUM:
        t = np.clip((l0 - STROKE_DELTA * 0.5 - lum) / (STROKE_DELTA * 0.5), 0.0, 1.0)
        w = t.astype(np.float32)
        rim = cv2.dilate((~opaque).astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2)
        w = np.maximum(w, (rim > 0).astype(np.float32))
        w[~opaque] = 0.0
        return w, {"mode": "light_fill", "fill_lum": l0}
    w = opaque.astype(np.float32)
    return w, {"mode": "ink_fallback", "fill_lum": l0}


def fill_colour(
    body_rgba: np.ndarray, info: dict, *, force: bool = False
) -> tuple[int, int, int] | None:
    """Dominant opaque colour of a light-filled body (None for line / dark
    unless ``force``, which returns the dominant opaque colour regardless —
    used for a DARK filled body, whose body colour is its ink)."""

    if not force and info.get("mode") != "light_fill":
        return None
    if not (body_rgba[:, :, 3] > 8).any():
        return None
    a = body_rgba[:, :, 3]
    rgb = body_rgba[:, :, :3][a > 8]
    q = (rgb >> 6).astype(np.int32)
    key = q[:, 0] * 16 + q[:, 1] * 4 + q[:, 2]
    mode_key = int(np.bincount(key, minlength=64).argmax())
    members = rgb[key == mode_key]
    return tuple(int(v) for v in members.mean(axis=0).round())


def ink_stroke_width(body_rgba: np.ndarray, stroke_w: np.ndarray, fallback: float) -> float:
    """Measured width (px) of the body's OUTLINE STROKE: distance-transform
    ridge width of the half-coverage stroke pixels (``alpha >= 128`` and
    stroke weight > 0.5). For a line body this is the guide's line width;
    for a FILLED body it is the outline ring (the guide's ``line_width``
    there is the width of the whole shape)."""

    core = ((body_rgba[:, :, 3] >= 128) & (stroke_w > 0.5)).astype(np.uint8) * 255
    if not core.any():
        return float(fallback)
    dist = _distance_transform_roi(core)
    return float(max(1.0, _ridge_width(dist, core, float(fallback))))


def body_interior_mask(
    body_rgba: np.ndarray, stroke_w: np.ndarray, line_width: float
) -> np.ndarray:
    """(h, w) uint8 0..255 strength of the body INTERIOR in body-local
    coords: the region enclosed by the outer contour of the body's main ink
    component (consolidated with the same capped closing kernel as the
    guide builder) MINUS its stroke pixels (soft: ``1 - stroke weight``).
    Tail ink over this region would lie INSIDE the body outline. A light
    fill counts as interior; a broken line outline that the closing cannot
    seal yields (almost) no interior (-> no clipping, the prior behaviour).
    Data-driven; no style branches."""

    import cv2  # noqa: PLC0415

    alpha = body_rgba[:, :, 3]
    ink = ((alpha > 8).astype(np.uint8)) * 255
    h, w = alpha.shape
    if not ink.any():
        return np.zeros((h, w), np.uint8)
    k = int(max(3, min(INTERIOR_CLOSE_MAX_K, round(float(line_width) * 2))) | 1)
    consolidated = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    count, labels, stats, _c = cv2.connectedComponentsWithStats(consolidated, connectivity=8)
    if count <= 1:
        return np.zeros((h, w), np.uint8)
    main_label = int(np.argmax(stats[1:, cv2.CC_STAT_AREA])) + 1
    main_mask = ((labels == main_label).astype(np.uint8)) * 255
    contours, _hier = cv2.findContours(main_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return np.zeros((h, w), np.uint8)
    enclosed = np.zeros((h, w), np.uint8)
    cv2.fillPoly(enclosed, [max(contours, key=cv2.contourArea)], 255)
    interior = enclosed.astype(np.float32) * (1.0 - np.clip(stroke_w, 0.0, 1.0))
    return np.clip(np.rint(interior), 0, 255).astype(np.uint8)


def ink_cut(
    keep: np.ndarray,
    stroke_roi: np.ndarray,
    tail_alpha_roi: np.ndarray | None,
) -> np.ndarray:
    """Ink-only cut (float32 0..1): the band cut on the body's STROKE
    pixels — the stroke core (weight > 0.5) grown by 1 px so the outline's
    own AA halo goes with it (no faint remnant line across the opening) —
    and NEVER on a pixel the tail ink hides (``tail_protect_mask``): the
    outline under a leg stays solid and the leg fuses with it; the cut is
    binary there (no half-cut / half-covered seam). All inputs are
    ROI-sized (the band rect)."""

    cut = ink_cut_src(keep, stroke_roi)
    protect = tail_protect_mask(tail_alpha_roi)
    if protect is not None:
        cut[protect] = 0.0
    return cut


def ink_cut_src(keep: np.ndarray, stroke_roi: np.ndarray) -> np.ndarray:
    """The opened-stroke footprint BEFORE tail protection (float32 0..1):
    band cut x stroke core grown by 1 px. Also the seed of the junction
    fill sweep, which spans the whole base (legs included)."""

    import cv2  # noqa: PLC0415

    core = cv2.dilate((stroke_roi > 0.5).astype(np.uint8), np.ones((3, 3), np.uint8))
    return (1.0 - keep).astype(np.float32, copy=False) * core.astype(np.float32)


def tail_protect_mask(tail_alpha_roi: np.ndarray | None) -> np.ndarray | None:
    """Bool ROI mask of the pixels the tail ink touches (coverage > 0.02,
    the leg's full footprint including its AA fringe) plus 1 px around the
    leg's CORE (coverage > 0.5) — never cut, never repainted: the outline
    under and right next to a leg stays solid, so the leg and the outline
    fuse (a binary cut: no half-cut / half-covered seam pixels, no 1 px
    nick at the leg / outline corner). Growing only the core keeps the
    protection tight around thin legs. None without a tail."""

    if tail_alpha_roi is None:
        return None
    import cv2  # noqa: PLC0415

    core = cv2.dilate((tail_alpha_roi > 0.5).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    return (tail_alpha_roi > 0.02) | core


def junction_fill_cover(
    cut: np.ndarray,
    keep: np.ndarray,
    stroke_roi: np.ndarray,
    normal: tuple[float, float],
    inset_px: float,
    body_line_width: float,
    *,
    sweep_src: np.ndarray | None = None,
) -> np.ndarray:
    """``junction_fill_inset_px``: the opened-stroke cut swept along the
    INWARD outline normal by ``inset_px`` (directional line kernel — no
    tangential/outward growth), clipped to the band ROI (the arrays are the
    ROI), minimal 3x3 feather, never over a living stroke pixel
    (``stroke > 0.5 and keep > 0.5``). Capped at ``JUNCTION_INSET_MAX_LW``
    x the body line width. ``inset_px`` <= 0 returns ``cut`` unchanged.
    ``sweep_src`` (optional) seeds the sweep instead of ``cut`` — e.g. the
    opened-stroke footprint before tail protection, so the fill extends
    into the body along the WHOLE base, legs included; the final cover on
    living stroke pixels is always ``cut``."""

    inset = float(inset_px)
    seed = cut if sweep_src is None else sweep_src
    if inset <= 0.0 or not seed.any():
        return cut
    import cv2  # noqa: PLC0415

    cap = JUNCTION_INSET_MAX_LW * max(1.5, min(12.0, body_line_width))
    length = round(min(inset, cap))
    if length < 1:
        return cut
    # cv2.dilate shifts the shape OPPOSITE to the kernel offsets, so the
    # kernel line points along the OUTWARD normal to sweep the cut inward
    k = 2 * length + 1
    kernel = np.zeros((k, k), np.uint8)
    ex = round(length + float(normal[0]) * length)
    ey = round(length + float(normal[1]) * length)
    cv2.line(kernel, (length, length), (ex, ey), 1, 1)
    kernel[length, length] = 0  # shifted copies only: original coverage kept
    if not kernel.any():
        return cut
    swept = cv2.dilate((seed > 0.05).astype(np.uint8), kernel)
    cover = np.maximum(cut, swept.astype(np.float32))
    cover = np.maximum(cut, cv2.blur(cover, (3, 3)))
    living = (stroke_roi > 0.5) & (keep > 0.5)
    cover[living] = cut[living]
    return cover


def junction_weld_mask(
    ink: np.ndarray,
    tail_ink: np.ndarray,
    outline: np.ndarray,
    interior: np.ndarray,
    radius: int,
) -> np.ndarray:
    """Bool mask of the small gaps to WELD shut at the junction: pixels a
    morphological closing of ``ink`` (radius ``radius`` px) fills that lie
    within ``radius + 1`` px of BOTH the tail ink and the kept outline and
    are not inside the body interior. Closes the notch left between a leg
    that crosses the outline at an angle and the (hard) cut end, and the
    concave corner where a leg leaves the outline; an opening wider than
    ``2 * radius`` px stays open. All inputs are ROI-sized bool arrays."""

    import cv2  # noqa: PLC0415

    r = max(1, int(radius))
    if not ink.any() or not tail_ink.any() or not outline.any():
        return np.zeros(ink.shape, bool)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    closed = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_CLOSE, k) > 0
    cand = closed & ~ink
    if not cand.any():
        return cand
    reach = np.ones((2 * r + 3, 2 * r + 3), np.uint8)
    near_tail = cv2.dilate(tail_ink.astype(np.uint8), reach) > 0
    near_outline = cv2.dilate(outline.astype(np.uint8), reach) > 0
    return cand & near_tail & near_outline & ~interior


def _nonzero_bbox(a: np.ndarray, eps: float = 0.003) -> tuple[int, int, int, int] | None:
    """(x, y, w, h) bounding box of ``a > eps`` or None."""

    rows = np.nonzero((a > eps).any(axis=1))[0]
    if rows.size == 0:
        return None
    cols = np.nonzero((a > eps).any(axis=0))[0]
    return int(cols[0]), int(rows[0]), int(cols[-1] - cols[0] + 1), int(rows[-1] - rows[0] + 1)


# ── open_ink ──────────────────────────────────────────────────────────


@dataclass
class InkOpeningComposer(RigComposer):
    """RigComposer whose opening only removes stroke pixels not hidden by
    the tail; the arc is derived from the POSED tail legs and the cut
    outline ends are bridged to the leg endpoints."""

    stroke_info: dict = field(default_factory=dict, init=False)
    #: measured OUTLINE-STROKE width (px): band depth reference (the guide's
    #: ``line_width`` is the whole-shape width for a filled body)
    ink_width: float = field(default=2.0, init=False)
    _stroke_canvas: np.ndarray = field(init=False)
    _interior_canvas: np.ndarray = field(init=False)
    #: junction end bridges of the LAST frame as a straight-alpha patch (for
    #: the compositor preview; ``output`` carries them painted in)
    _last_bridge: TailPatch | None = field(default=None, init=False)
    #: gap centre (u) of the last frame — see ``opening_check_u``
    _last_gap_u: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        w, h = self.canvas_size
        s, info = stroke_mask(self.body_rgba)
        self.stroke_info = info
        self.ink_width = ink_stroke_width(self.body_rgba, s, self.guide.line_width)
        # canvas-sized stroke weight map (float32) and body-interior map
        # (uint8) — derived, not persisted
        self._stroke_canvas = np.zeros((h, w), np.float32)
        self._interior_canvas = np.zeros((h, w), np.uint8)
        interior = body_interior_mask(self.body_rgba, s, self.guide.line_width)
        bx, by = round(self.guide.origin[0]), round(self.guide.origin[1])
        bh, bw = s.shape
        x0, y0 = max(0, bx), max(0, by)
        x1, y1 = min(w, bx + bw), min(h, by + bh)
        if x1 > x0 and y1 > y0:
            self._stroke_canvas[y0:y1, x0:x1] = s[y0 - by : y1 - by, x0 - bx : x1 - bx]
            self._interior_canvas[y0:y1, x0:x1] = interior[y0 - by : y1 - by, x0 - bx : x1 - bx]

    # -- helpers ----------------------------------------------------------
    def _stroke_roi(self, rect: tuple[int, int, int, int]) -> np.ndarray:
        """Stroke weight for ``rect`` (read-only view, cached per rect: the
        band rect only changes when the root moves)."""

        cached = getattr(self, "_stroke_roi_cache", None)
        if cached is not None and cached[0] == rect:
            return cached[1]
        x0, y0, w, h = rect
        cw, ch = self.canvas_size
        out = np.zeros((h, w), np.float32)
        sx0, sy0 = max(0, x0), max(0, y0)
        sx1, sy1 = min(cw, x0 + w), min(ch, y0 + h)
        if sx1 > sx0 and sy1 > sy0:
            out[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0] = self._stroke_canvas[sy0:sy1, sx0:sx1]
        out.setflags(write=False)
        self._stroke_roi_cache = (rect, out)
        return out

    @staticmethod
    def _tail_alpha_roi(
        rect: tuple[int, int, int, int], tail: TailPatch | None
    ) -> np.ndarray | None:
        if tail is None:
            return None
        x0, y0, w, h = rect
        ta = np.zeros((h, w), np.float32)
        tx0, ty0 = max(x0, tail.x0), max(y0, tail.y0)
        tx1 = min(x0 + w, tail.x0 + tail.rgba.shape[1])
        ty1 = min(y0 + h, tail.y0 + tail.rgba.shape[0])
        if tx1 > tx0 and ty1 > ty0:
            ta[ty0 - y0 : ty1 - y0, tx0 - x0 : tx1 - x0] = tail.rgba[
                ty0 - tail.y0 : ty1 - tail.y0, tx0 - tail.x0 : tx1 - tail.x0, 3
            ].astype(np.float32) * (1.0 / 255.0)
        return ta

    def _local_stroke_thickness(self, pose: TailPose, base_width: float) -> float:
        """Thickness (px) of the outline stroke around the root: 2 x the
        largest distance-transform radius of the stroke core inside a window
        of +/- (base width / 2 + 2 x ink width + 8) px around the root
        point (page coords). Capped at 48 px; ``ink_width`` when there is no
        stroke there."""

        import cv2  # noqa: PLC0415

        g = self.guide
        p = g.point_at(pose.root_u)
        cx, cy = p[0] + g.origin[0], p[1] + g.origin[1]
        half = math.ceil(base_width * 0.5 + 2.0 * self.ink_width + 8.0)
        h, w = self.body_canvas.shape[:2]
        x0, y0 = max(0, int(cx) - half), max(0, int(cy) - half)
        x1, y1 = min(w, int(cx) + half + 1), min(h, int(cy) + half + 1)
        if x1 <= x0 or y1 <= y0:
            return float(self.ink_width)
        core = (
            (self.body_canvas[y0:y1, x0:x1, 3] >= 128) & (self._stroke_canvas[y0:y1, x0:x1] > 0.5)
        ).astype(np.uint8)
        if not core.any():
            return float(self.ink_width)
        # pad so the window border does not count as background
        padded = np.pad(core, 1, mode="edge")
        dist = cv2.distanceTransform(padded, cv2.DIST_L2, 3)[1:-1, 1:-1]
        return float(min(48.0, max(self.ink_width, 2.0 * float(dist.max()))))

    def _interior_roi(self, rect: tuple[int, int, int, int]) -> np.ndarray:
        """Body-interior strength (uint8) for ``rect`` (page coords; zero
        outside the canvas)."""

        x0, y0, w, h = rect
        cw, ch = self.canvas_size
        out = np.zeros((h, w), np.uint8)
        sx0, sy0 = max(0, x0), max(0, y0)
        sx1, sy1 = min(cw, x0 + w), min(ch, y0 + h)
        if sx1 > sx0 and sy1 > sy0:
            out[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0] = self._interior_canvas[sy0:sy1, sx0:sx1]
        return out

    def _clip_tail(
        self, tail: TailPatch | None, pose: TailPose, recipe: CompositingRecipe
    ) -> TailPatch | None:
        """Keep tail ink OUT of the body interior: the posed patch's alpha is
        multiplied by ``1 - interior`` so the legs end at the outline's
        inner edge instead of running on inside the body (``tail_under``
        needs no clip: the body covers it). Transparent pixels carry no
        colour. A new patch is returned; the deformer output is untouched."""

        del pose
        if tail is None or recipe.z_order == "tail_under":
            return tail
        interior = self._interior_roi(tail.rect)
        if not interior.any():
            return tail
        rgba = tail.rgba.copy()
        a = rgba[:, :, 3].astype(np.float32) * (1.0 - interior.astype(np.float32) * (1.0 / 255.0))
        a8 = np.clip(np.rint(a), 0, 255).astype(np.uint8)
        rgba[:, :, 3] = a8
        rgba[a8 == 0, :3] = 0
        return TailPatch(tail.x0, tail.y0, rgba)

    def _band_depth_extra(self) -> float:
        """Extra band ROI padding (px) the subclass needs inside the body
        for what it paints within the band (e.g. the junction fill inset
        sweep). It pads the ROI only — never the cut. The base cut needs
        none."""

        return 0.0

    def _interior_at(self, point: tuple[float, float]) -> int:
        """Body-interior strength (0..255) at a page point (0 off-canvas)."""

        x, y = int(point[0]), int(point[1])
        h, w = self._interior_canvas.shape
        if 0 <= x < w and 0 <= y < h:
            return int(self._interior_canvas[y, x])
        return 0

    def _nearest_remaining_stroke(
        self, point: tuple[float, float], radius: float
    ) -> tuple[float, float] | None:
        """Page position of the uncut outline-stroke pixel (alpha > 128,
        stroke weight > 0.5, band keep > 0.5) nearest to ``point`` within
        ``radius`` — the REAL end of the remaining outline (the guide point
        is a smoothed approximation)."""

        h, w = self.body_canvas.shape[:2]
        r = math.ceil(radius)
        x0, y0 = max(0, int(point[0]) - r), max(0, int(point[1]) - r)
        x1, y1 = min(w, int(point[0]) + r + 1), min(h, int(point[1]) + r + 1)
        if x1 <= x0 or y1 <= y0:
            return None
        sel = (self.body_canvas[y0:y1, x0:x1, 3] > 128) & (self._stroke_canvas[y0:y1, x0:x1] > 0.5)
        mask = self._last_mask
        if mask is not None:
            mr = _clip_rect(mask.rect, w, h)
            if mr is not None:
                ix0, iy0 = max(mr[0], x0), max(mr[1], y0)
                ix1, iy1 = min(mr[0] + mr[2], x1), min(mr[1] + mr[3], y1)
                if ix1 > ix0 and iy1 > iy0:
                    keep = mask.keep[iy0 - mask.y0 : iy1 - mask.y0, ix0 - mask.x0 : ix1 - mask.x0]
                    sel[iy0 - y0 : iy1 - y0, ix0 - x0 : ix1 - x0] &= keep > 0.5
        if not sel.any():
            return None
        ys, xs = np.nonzero(sel)
        d2 = (xs + x0 + 0.5 - point[0]) ** 2 + (ys + y0 + 0.5 - point[1]) ** 2
        k = int(np.argmin(d2))
        return (float(xs[k] + x0 + 0.5), float(ys[k] + y0 + 0.5))

    def preview_tail_patch(self) -> tuple[tuple[int, int, int, int], np.ndarray] | None:
        """The clipped posed tail patch plus the junction end bridges drawn
        over it — what the compositor preview shows as the tail layer."""

        base = super().preview_tail_patch()
        bridge = getattr(self, "_last_bridge", None)
        if bridge is None:
            return base
        br = _clip_rect(bridge.rect, *self.canvas_size)
        if br is None:
            return base
        rect = br if base is None else _clip_rect(_rect_union(base[0], br), *self.canvas_size)
        if rect is None:
            return base
        x0, y0, w, h = rect
        out = np.zeros((h, w, 4), np.uint8)
        if base is not None:
            (bx0, by0, bw, bh), brgba = base
            out[by0 - y0 : by0 - y0 + bh, bx0 - x0 : bx0 - x0 + bw] = brgba
        bx0, by0, bw, bh = br
        sub = out[by0 - y0 : by0 - y0 + bh, bx0 - x0 : bx0 - x0 + bw]
        stamp = bridge.rgba[
            by0 - bridge.y0 : by0 - bridge.y0 + bh, bx0 - bridge.x0 : bx0 - bridge.x0 + bw
        ]
        _over(sub, stamp)
        return rect, out

    def _posed_arc(
        self, pose: TailPose, recipe: CompositingRecipe
    ) -> tuple[tuple[float, float], tuple[float, object], tuple[float, object]] | None:
        """((centre_u, span_px), (u_left, base_left), (u_right, base_right))
        derived from the POSED base endpoints (the legs' OUTER corners)
        projected onto the outline — pure geometry. The band uses HARD ends
        there and ``ink_cut`` never touches a pixel the tail ink covers, so
        the opening is exactly the outline between the legs (the outline
        beyond a corner stays, the outline under a leg stays solid)."""

        try:
            bl, br = self._deformer.posed_base_endpoints(pose, recipe=recipe)
        except Exception:
            return None
        g = self.guide
        us = [g.nearest_u((p[0] - g.origin[0], p[1] - g.origin[1])) for p in (bl, br)]
        du = (us[1] - us[0] + 0.5) % 1.0 - 0.5
        centre = (us[0] + du * 0.5) % 1.0
        span = abs(du) * g.perimeter
        lw = max(1.5, min(12.0, self.rig.rig_space().line_width))
        span = max(span, 2.0 * lw)
        return (centre, span), (us[0], bl), (us[1], br)

    def _opening_mask(
        self, pose: TailPose, recipe: CompositingRecipe, tail: TailPatch | None
    ) -> MaskPatch | None:
        arc_info = self._posed_arc(pose, recipe)
        band_recipe = recipe
        if arc_info is not None:
            # the narrow posed-leg arc must still cut through the outline's
            # LOCAL excursion measured over the REGISTERED base width window.
            # Depth = RAW excursion + the measured OUTLINE-STROKE width (+
            # what the subclass sweeps inside), NOT ``guide.line_width`` /
            # 2: for a filled body that is the whole shape's half width and
            # the band would cut the far side of the body too.
            rig_px = self.rig.rig_space()
            reg_half_u = (rig_px.base_width * 0.5 + recipe.opening_extra_px) / max(
                1e-9, self.guide.perimeter
            )
            raw_exc = max(
                0.0,
                self.guide.excursion_over(pose.root_u - reg_half_u, pose.root_u + reg_half_u)
                - self.guide.line_width * 0.5,
            )
            # the outline stroke is cut through its LOCAL thickness at the
            # root (a brush outline can be 3-4x thicker than the body's
            # typical line; a cut that stops inside a thick band leaves the
            # tail buried in it) + half an ink width (>= 1 px) of slack so a
            # locally thicker stroke is not half-cut at the across feather
            thick = max(self.ink_width, self._local_stroke_thickness(pose, rig_px.base_width))
            depth_needed = raw_exc + thick + max(1.0, 0.5 * self.ink_width)
            if depth_needed > recipe.opening_depth_px:
                band_recipe = replace(recipe, opening_depth_px=float(depth_needed))
            # the posed arc places its ends explicitly: no extra half-arc
            band_recipe = replace(band_recipe, opening_extra_px=0.0)
        # the cut spans exactly the visible gap between the legs (the inner
        # edges of the outline each leg's ink covers; corner projections
        # where a leg does not touch), with HARD ends — a fade would only
        # expose a half-cut strip. The ROI is padded (not the cut) for what
        # the subclass paints inside the band (junction fill sweep), so the
        # cut never depends on it.
        arc = None
        self._last_gap_u = None
        if arc_info is not None:
            left, right, gap_u = self._gap_extents(arc_info, tail)
            arc = (gap_u, right - left)
            self._last_gap_u = gap_u
        base = self._band_mask(
            pose,
            band_recipe,
            arc=arc,
            ink_width=self.ink_width,
            end_fade_px=0.0,
            roi_pad_px=self._band_depth_extra(),
        )
        if base is None:
            return None
        rect = (base.x0, base.y0, base.keep.shape[1], base.keep.shape[0])
        tail_roi = None
        if recipe.z_order != "tail_under":
            tail_roi = self._tail_alpha_roi(rect, tail)
        src = ink_cut_src(base.keep, self._stroke_roi(rect))
        cut = src.copy()
        protect = tail_protect_mask(tail_roi)
        if protect is not None:
            cut[protect] = 0.0
        return MaskPatch(base.x0, base.y0, (1.0 - cut).astype(np.float32), sweep_src=src)

    def _gap_extents(
        self,
        arc_info: tuple[tuple[float, float], tuple[float, object], tuple[float, object]],
        tail: TailPatch | None,
    ) -> tuple[float, float, float]:
        """(left_px, right_px, gap_u): the visible gap between the legs as
        signed along-outline offsets (px) from the geometric arc centre —
        the INNER edges of the outline pixels each leg's ink actually
        covers (within 2 tail line widths of the corner projections) — and
        the outline position of the gap's middle. A side the tail does not
        touch keeps its corner projection. Legs that meet leave a minimal
        gap of one tail line width each way around their meeting point.
        The opening is cut exactly between these edges (and only there),
        and the structural check samples the gap's middle."""

        (centre, _span), (ul, _bl), (ur, _br) = arc_info
        g = self.guide
        perimeter = max(1e-9, g.perimeter)
        half_raw = abs((ur - ul + 0.5) % 1.0 - 0.5) * perimeter * 0.5
        tail_lw = max(1.5, min(12.0, self.rig.rig_space().line_width))
        left, right = -half_raw, half_raw
        if tail is not None and self.recipe.z_order != "tail_under":
            rect = _clip_rect(tail.rect, *self.canvas_size)
            if rect is not None:
                x0, y0, w, h = rect
                ta = tail.rgba[y0 - tail.y0 : y0 - tail.y0 + h, x0 - tail.x0 : x0 - tail.x0 + w, 3]
                covered = (
                    (ta > 128)
                    & (self._stroke_canvas[y0 : y0 + h, x0 : x0 + w] > 0.5)
                    & (self.body_canvas[y0 : y0 + h, x0 : x0 + w, 3] > 128)
                )
                if covered.any():
                    ys, xs = np.nonzero(covered)
                    px = xs.astype(np.float64) + x0 - g.origin[0]
                    py = ys.astype(np.float64) + y0 - g.origin[1]
                    pts = g.outline
                    d2 = (px[:, None] - pts[None, :, 0]) ** 2 + (py[:, None] - pts[None, :, 1]) ** 2
                    u = (g.cum[np.argmin(d2, axis=1)] / perimeter) % 1.0
                    off = ((u - centre + 0.5) % 1.0 - 0.5) * perimeter
                    off = off[np.abs(off) <= half_raw + 2.0 * tail_lw]
                    neg = off[off < 0.0]
                    pos = off[off > 0.0]
                    if neg.size:
                        left = float(neg.max())
                    if pos.size:
                        right = float(pos.min())
        if right - left < 2.0 * tail_lw:
            mid = (left + right) * 0.5
            left, right = mid - tail_lw, mid + tail_lw
        gap_u = (centre + (left + right) * 0.5 / perimeter) % 1.0
        return left, right, gap_u

    def opening_check_u(self) -> float | None:
        """``u`` of the last frame's gap centre (see ``_gap_centre_u``) —
        where "is the outline open at the root" should be sampled; None
        before the first update."""

        return self._last_gap_u

    # -- junction end bridging ------------------------------------------
    def _post_compose(  # noqa: PLR0915 - one bridging pipeline
        self, pose: TailPose, recipe: CompositingRecipe, tail: TailPatch | None
    ) -> None:
        """Connect each END of the opened outline arc to the nearest POSED
        tail base endpoint with a tapered stroke in the body's local ink
        colour. Skipped when the opening is off, the tail is missing, the
        pair already touches, or is implausibly far (misalignment guard)."""

        self._last_bridge = None
        if not recipe.opening_enabled or tail is None or recipe.z_order == "tail_under":
            return
        import cv2  # noqa: PLC0415

        rig = self.rig.rig_space()
        g = self.guide
        arc_info = self._posed_arc(pose, recipe)
        if arc_info is None:
            return
        _arc, (ul, bl2), (ur, br2) = arc_info
        pairs = []
        for u, bp in ((ul, bl2), (ur, br2)):
            pt = g.point_at(u)
            pairs.append(((pt[0] + g.origin[0], pt[1] + g.origin[1]), bp))
        body_lw = self._junction_stroke_width([p[0] for p in pairs])
        tail_lw = max(1.5, min(12.0, rig.line_width))
        width = max(2, round(min(max(tail_lw, body_lw), 2.5 * tail_lw)))
        limit = max(4.0 * rig.base_width, 48.0)
        colour = self._junction_ink_colour([p[0] for p in pairs])
        h, w = self.output.shape[:2]
        stamps: list[tuple[int, int, np.ndarray]] = []
        for (u_end, _bp), (a0, b0) in zip(((ul, bl2), (ur, br2)), pairs, strict=True):
            a, b = a0, b0
            # a corner INSIDE the body: the leg is clipped at the outline's
            # inner edge, there is no gap to bridge
            if self._interior_at(b) >= 128:
                continue
            # bridge from the REAL remaining outline end (nearest uncut
            # stroke pixel), not from the smoothed guide point
            real = self._nearest_remaining_stroke(b, min(limit, max(8.0, 3.0 * width)))
            if real is not None:
                a = real
            d = math.hypot(a[0] - b[0], a[1] - b[1])
            if d <= max(1.5, 0.25 * width) or d > limit:
                continue
            ext = min(width * 0.5 + recipe.opening_feather_px, d * 0.5)
            ux, uy = (b[0] - a[0]) / d, (b[1] - a[1]) / d
            b = (b[0] + ux * ext, b[1] + uy * ext)
            x0 = max(0, int(min(a[0], b[0]) - width - 2))
            y0 = max(0, int(min(a[1], b[1]) - width - 2))
            x1 = min(w, int(max(a[0], b[0]) + width + 3))
            y1 = min(h, int(max(a[1], b[1]) + width + 3))
            if x1 <= x0 or y1 <= y0:
                continue
            _p2, t_end, _n2 = g.frame_at(u_end)
            half_w = width * 0.5 + 0.5
            base1 = (a[0] + t_end[0] * half_w - x0, a[1] + t_end[1] * half_w - y0)
            base2 = (a[0] - t_end[0] * half_w - x0, a[1] - t_end[1] * half_w - y0)
            apex = (b[0] - x0, b[1] - y0)
            stamp = np.zeros((y1 - y0, x1 - x0), np.uint8)
            tri = np.asarray([base1, base2, apex], np.float32)
            cv2.fillConvexPoly(stamp, np.round(tri).astype(np.int32), 255, cv2.LINE_AA)
            cv2.line(
                stamp,
                (round(a[0] - x0), round(a[1] - y0)),
                (round(apex[0]), round(apex[1])),
                255,
                max(2, round(min(width, tail_lw))),
                cv2.LINE_AA,
            )
            sa = (stamp.astype(np.float32) * (1.0 / 255.0))[:, :, None]
            dst = self.output[y0:y1, x0:x1]
            # Both buffers are straight RGBA: transparent destination RGB
            # must not darken the antialiased edge of a coloured bridge.
            bridge = np.empty_like(dst)
            bridge[:, :, :3] = colour
            bridge[:, :, 3] = stamp
            _over(dst, bridge)
            stamps.append((x0, y0, sa[:, :, 0]))
        # weld the small gaps left between a leg and the cut outline end
        # (painted AFTER the bridges, from the composed ink of this frame)
        weld = self._junction_weld(tail, tail_lw, colour)
        if weld is not None:
            stamps.append(weld)
        if stamps:
            # the same stamps as a straight-alpha patch for the compositor
            # preview (``preview_tail_patch``); ``output`` above is unchanged
            bx0 = min(s[0] for s in stamps)
            by0 = min(s[1] for s in stamps)
            bx1 = max(s[0] + s[2].shape[1] for s in stamps)
            by1 = max(s[1] + s[2].shape[0] for s in stamps)
            rgba = np.zeros((by1 - by0, bx1 - bx0, 4), np.uint8)
            rgba[:, :, :3] = np.asarray(colour, np.uint8)
            for sx, sy, sa0 in stamps:
                view = rgba[
                    sy - by0 : sy - by0 + sa0.shape[0], sx - bx0 : sx - bx0 + sa0.shape[1], 3
                ]
                view[...] = np.maximum(view, np.clip(np.rint(sa0 * 255.0), 0, 255).astype(np.uint8))
            rgba[rgba[:, :, 3] == 0, :3] = 0
            self._last_bridge = TailPatch(bx0, by0, rgba)

    def _junction_weld(
        self, tail: TailPatch, tail_lw: float, colour: tuple[int, int, int]
    ) -> tuple[int, int, np.ndarray] | None:
        """Weld the small notches between the legs and the cut outline ends
        (``junction_weld_mask`` over this frame's composed ink in the band
        ROI) with the local ink colour: painted into ``output`` and returned
        as a stamp ``(x0, y0, coverage)`` for the preview tail patch."""

        mask = self._last_mask
        if mask is None:
            return None
        mr = _clip_rect(mask.rect, *self.canvas_size)
        if mr is None:
            return None
        x0, y0, w, h = mr
        out = self.output[y0 : y0 + h, x0 : x0 + w]
        lum = 0.299 * out[:, :, 0] + 0.587 * out[:, :, 1] + 0.114 * out[:, :, 2]
        ink = (out[:, :, 3] > 128) & (lum < 128)
        ta = self._tail_alpha_roi(mr, tail)
        if ta is None:
            return None
        keep = mask.keep[y0 - mask.y0 : y0 - mask.y0 + h, x0 - mask.x0 : x0 - mask.x0 + w]
        outline = (
            (self._stroke_canvas[y0 : y0 + h, x0 : x0 + w] > 0.5)
            & (self.body_canvas[y0 : y0 + h, x0 : x0 + w, 3] > 128)
            & (keep > 0.5)
        )
        radius = max(1, min(3, round(0.4 * float(tail_lw))))
        weld = junction_weld_mask(ink, ta > 0.5, outline, self._interior_roi(mr) >= 128, radius)
        if not weld.any():
            return None
        out[weld, :3] = np.asarray(colour, np.uint8)
        out[weld, 3] = 255
        bb = _nonzero_bbox(weld.astype(np.float32), 0.5)
        if bb is None:
            return None
        bx, by, bw, bh = bb
        return (x0 + bx, y0 + by, weld[by : by + bh, bx : bx + bw].astype(np.float32))

    def _junction_stroke_width(self, arc_ends) -> float:
        import cv2  # noqa: PLC0415

        h, w = self.body_canvas.shape[:2]
        best = 0.0
        for ax, ay in arc_ends:
            x0, y0 = max(0, int(ax) - 12), max(0, int(ay) - 12)
            x1, y1 = min(w, int(ax) + 13), min(h, int(ay) + 13)
            if x1 <= x0 or y1 <= y0:
                continue
            mask = (
                (self.body_canvas[y0:y1, x0:x1, 3] > 128)
                & (self._stroke_canvas[y0:y1, x0:x1] > 0.5)
            ).astype(np.uint8)
            if not mask.any():
                continue
            dist = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
            best = max(best, float(dist.max()) * 2.0)
        return max(1.5, min(12.0, best)) if best > 0 else 0.0

    def _junction_ink_colour(self, arc_ends) -> tuple[int, int, int]:
        samples = []
        h, w = self.body_canvas.shape[:2]
        for ax, ay in arc_ends:
            x0, y0 = max(0, int(ax) - 6), max(0, int(ay) - 6)
            x1, y1 = min(w, int(ax) + 7), min(h, int(ay) + 7)
            if x1 <= x0 or y1 <= y0:
                continue
            region = self.body_canvas[y0:y1, x0:x1]
            stroke_w = self._stroke_canvas[y0:y1, x0:x1]
            sel = (region[:, :, 3] > 128) & (stroke_w > 0.5)
            if sel.any():
                samples.append(region[sel][:, :3])
        if not samples:
            return (0, 0, 0)
        allpx = np.concatenate(samples, axis=0).astype(np.float32)
        return tuple(int(v) for v in np.median(allpx, axis=0))


# ── open_ink_fill ─────────────────────────────────────────────────────


@dataclass
class InkFillOpeningComposer(InkOpeningComposer):
    """``open_ink`` + (a) opened stroke pixels become the fill colour
    instead of transparent, (b) the closed tail interior is painted with
    the fill colour under the tail strokes, (c) ``junction_fill_inset_px``
    extends the junction fill INTO the body. Bodies without a light fill
    behave exactly like ``open_ink``."""

    junction_fill_inset_px: float = 0.0
    fill_rgb: tuple[int, int, int] | None = field(default=None, init=False)
    fill_skipped: int = field(default=0, init=False)
    #: tail-interior fill of the LAST frame: (canvas rect, bool mask in that
    #: rect) — drawn under the tail strokes in ``preview_tail_patch``
    _last_interior: tuple[tuple[int, int, int, int], np.ndarray] | None = field(
        default=None, init=False
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if not (
            isinstance(self.junction_fill_inset_px, (int, float))
            and np.isfinite(self.junction_fill_inset_px)
            and self.junction_fill_inset_px >= 0.0
        ):
            raise ValueError("junction_fill_inset_px must be a finite value >= 0")
        self.fill_rgb = fill_colour(self.body_rgba, self.stroke_info)
        if (
            self.fill_rgb is None
            and self.stroke_info.get("mode") == "ink_fallback"
            and self.guide.fill_ratio >= DARK_FILL_MIN_RATIO
        ):
            # a DARK filled body (a black balloon: every opaque pixel is ink):
            # its body colour IS the ink, so the opening and the tail interior
            # are painted with it and the tail merges into the body. A
            # transparent opening here would show the layers below through
            # the tail and the junction (QA-2 finding, 2026-08-23). A line
            # body (small fill ratio) keeps the transparent opening.
            self.fill_rgb = fill_colour(self.body_rgba, self.stroke_info, force=True)

    def _band_depth_extra(self) -> float:
        """The junction fill inset sweeps INTO the body inside the band ROI:
        the ROI is padded by the sweep's CAP (not the current inset) so the
        band geometry — and therefore the cut — is identical for every
        inset value; only the sweep differs."""

        if self.recipe.z_order == "tail_under" or self.fill_rgb is None:
            return 0.0
        return float(JUNCTION_INSET_MAX_LW * max(1.5, min(12.0, self.guide.line_width)))

    # -- per-ROI pieces -----------------------------------------------------
    def _fill_cover(
        self, keep: np.ndarray, rect: tuple[int, int, int, int], pose: TailPose
    ) -> np.ndarray:
        """Coverage 0..1 (ROI) of the fill-colour repaint: the cut plus the
        inward junction sweep (unless ``tail_under``)."""

        cached = getattr(self, "_cover_cache", None)
        if cached is not None and cached[0] is keep and cached[1] == pose:
            return cached[2]
        cut = (1.0 - keep).astype(np.float32, copy=False)
        if self.recipe.z_order == "tail_under" or self.junction_fill_inset_px <= 0.0:
            cover = cut
        else:
            _pt, _t, n = self.guide.frame_at(pose.root_u)
            # the sweep is seeded by the opened-stroke footprint BEFORE tail
            # protection (the whole base, legs included); pixels the tail
            # touches are never repainted (the leg / outline fuse stays)
            src = self._sweep_src(rect, cut)
            cover = junction_fill_cover(
                cut,
                keep,
                self._stroke_roi(rect),
                (float(n[0]), float(n[1])),
                self.junction_fill_inset_px,
                self.guide.line_width,
                sweep_src=src,
            )
            protect = tail_protect_mask(self._tail_alpha_roi(rect, self._last_tail))
            if protect is not None:
                cover[protect] = cut[protect]
        # per-frame cache (same keep array object + same pose): update()
        # and body_region() share one computation
        self._cover_cache = (keep, pose, cover)
        return cover

    def _sweep_src(self, rect: tuple[int, int, int, int], cut: np.ndarray) -> np.ndarray:
        """The last mask's pre-protection opened-stroke footprint for
        ``rect`` (the canvas-clipped mask rect); ``cut`` when absent."""

        mask = self._last_mask
        if mask is None or mask.sweep_src is None:
            return cut
        x0, y0, w, h = rect
        return mask.sweep_src[y0 - mask.y0 : y0 - mask.y0 + h, x0 - mask.x0 : x0 - mask.x0 + w]

    def _paint_fill(self, sub: np.ndarray, cover: np.ndarray, fill: tuple[int, int, int]) -> None:
        """Blend ``fill`` into ``sub`` (uint8 RGBA view) with ``cover``;
        restricted to the bounding box of the non-zero cover (the opened
        stroke + inset is a thin band inside a possibly large ROI)."""

        bb = _nonzero_bbox(cover)
        if bb is None:
            return
        bx, by, bw, bh = bb
        c = cover[by : by + bh, bx : bx + bw][:, :, None]
        dst = sub[by : by + bh, bx : bx + bw]
        a = dst[:, :, 3].astype(np.float32)
        rgb = dst[:, :, :3].astype(np.float32)
        rgb = rgb * (1.0 - c) + np.asarray(fill, np.float32) * c
        # the repaint recolours EXISTING coverage only: the alpha is never
        # raised (no opaque fill created outside the body — a feathered
        # cover over a transparent pixel used to leave a grey, half-opaque
        # rim along the outer edge of the opening), and a partially covered
        # pixel (the outline's outer AA) fades with the cut instead
        a_new = np.where(a >= 255.0, a, a * (1.0 - c[:, :, 0]))
        dst[:, :, :3] = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
        dst[:, :, 3] = np.clip(np.rint(a_new), 0, 255).astype(np.uint8)
        dst[dst[:, :, 3] == 0, :3] = 0

    def update(  # noqa: PLR0912 - mirrors RigComposer.update
        self, pose: TailPose, *, opening: bool | None = None
    ) -> tuple[int, int, int, int] | None:
        recipe = self.recipe
        if opening is not None and opening != recipe.opening_enabled:
            recipe = replace(recipe, opening_enabled=opening)
        tail = self._deformer.render(pose, recipe=recipe, coarse_min_px=self.render_coarse_min_px())
        tail = self._clip_tail(tail, pose, recipe)
        mask = self._opening_mask(pose, recipe, tail)
        self._cover_cache = None
        self._last_interior = None
        new_rect = _rect_union(tail.rect if tail else None, mask.rect if mask else None)
        dirty = _clip_rect(_rect_union(self._last_rect, new_rect), *self.canvas_size)
        self._last_rect, self._last_pose, self._last_tail, self._last_mask = (
            new_rect,
            pose,
            tail,
            mask,
        )
        if dirty is None:
            return None
        x0, y0, w, h = dirty
        self.output[y0 : y0 + h, x0 : x0 + w] = self.body_canvas[y0 : y0 + h, x0 : x0 + w]
        fill = self.fill_rgb
        if mask is not None:
            mr = _clip_rect(mask.rect, *self.canvas_size)
            if mr is not None:
                mx0, my0, mw, mh = mr
                keep = mask.keep[
                    my0 - mask.y0 : my0 - mask.y0 + mh, mx0 - mask.x0 : mx0 - mask.x0 + mw
                ]
                sub = self.output[my0 : my0 + mh, mx0 : mx0 + mw]
                if fill is None:
                    bb = _nonzero_bbox(1.0 - keep)
                    if bb is not None:
                        bx, by, bw, bh = bb
                        k = keep[by : by + bh, bx : bx + bw]
                        s2 = sub[by : by + bh, bx : bx + bw]
                        s2[:, :, 3] = (s2[:, :, 3].astype(np.float32) * k).astype(np.uint8)
                else:
                    self._paint_fill(sub, self._fill_cover(keep, mr, pose), fill)
        if tail is not None:
            tr = _clip_rect(tail.rect, *self.canvas_size)
            if tr is not None:
                tx0, ty0, tw, th = tr
                patch = tail.rgba[
                    ty0 - tail.y0 : ty0 - tail.y0 + th, tx0 - tail.x0 : tx0 - tail.x0 + tw
                ]
                dst = self.output[ty0 : ty0 + th, tx0 : tx0 + tw]
                if fill is not None and recipe.z_order != "tail_under":
                    interior = self._tail_interior(patch)
                    if interior is not None:
                        # the tail's interior fill stays OUTSIDE the body too
                        interior &= self._interior_roi(tr) < 128
                        dst[interior, :3] = np.asarray(fill, np.uint8)
                        dst[interior, 3] = 255
                        self._last_interior = (tr, interior)
                if recipe.z_order == "tail_under":
                    body_part = dst.copy()
                    dst[...] = patch
                    _over(dst, body_part)
                else:
                    _over(dst, patch)
        self._post_compose(pose, recipe, tail)
        return dirty

    def _tail_interior(self, patch: np.ndarray) -> np.ndarray | None:
        """Interior of the tail in patch coords: the pristine tail's
        enclosed region carried through the SAME inverse map as the pixels."""

        import cv2  # noqa: PLC0415

        src = self._interior_src()
        lm = self._deformer.last_map
        if src is None or lm is None:
            self.fill_skipped += 1
            return None
        map_x, map_y, _px0, _py0 = lm
        if map_x.shape != patch.shape[:2]:
            self.fill_skipped += 1
            return None
        interior = cv2.remap(
            src, map_x, map_y, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0
        )
        # grown by 1 px after the (nearest) remap so the inner fringe of BOTH
        # legs is covered whatever way the rounding fell (the source mask is
        # eroded by 2 px, so the outermost ring of the silhouette stays out)
        interior = cv2.dilate((interior > 0).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        interior = interior & (patch[:, :, 3] < 255)
        if not interior.any():
            self.fill_skipped += 1
            return None
        return interior

    def _interior_src(self) -> np.ndarray | None:
        cached = getattr(self, "_interior_cache", None)
        if cached is not None:
            return cached if cached.size else None
        import cv2  # noqa: PLC0415

        ink = (self.tail_rgba[:, :, 3] > 8).astype(np.uint8) * 255
        kk = int(max(3, round(self.rig.base_width * 1.2)) | 1)
        closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((kk, kk), np.uint8))
        contours, _h = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            self._interior_cache = np.zeros(0, np.uint8)
            return None
        fill = np.zeros_like(ink)
        cv2.fillPoly(fill, [max(contours, key=cv2.contourArea)], 255)
        # the fill goes UNDER the strokes too (it is painted before the tail
        # is composited, so opaque ink hides it) — in particular under the
        # legs' INNER anti-aliased fringe, which otherwise stays
        # semi-transparent and shows the layers below as a coloured seam
        # along the inside of the legs (QA-3 finding, 2026-08-23). Only the
        # outermost ring of the silhouette (the legs' OUTER fringe, which must
        # keep blending with whatever is outside the tail) is left out: eroded
        # by 2 px here, grown back by 1 px after the posed remap.
        interior = cv2.erode(fill, np.ones((5, 5), np.uint8))
        interior = (interior > 0).astype(np.uint8)
        self._interior_cache = interior
        return interior if interior.any() else None

    def preview_tail_patch(self) -> tuple[tuple[int, int, int, int], np.ndarray] | None:
        """Tail layer of the compositor preview: the closed tail interior
        painted with the body's fill colour UNDER the tail strokes (as
        ``update`` composes it), then the tail patch and the end bridges."""

        base = super().preview_tail_patch()
        li = self._last_interior
        if base is None or li is None or self.fill_rgb is None:
            return base
        (x0, y0, w, h), rgba = base
        (tx0, ty0, tw, th), interior = li
        ix0, iy0 = max(tx0, x0), max(ty0, y0)
        ix1, iy1 = min(tx0 + tw, x0 + w), min(ty0 + th, y0 + h)
        if ix1 <= ix0 or iy1 <= iy0:
            return base
        out = np.zeros((h, w, 4), np.uint8)
        sub = out[iy0 - y0 : iy1 - y0, ix0 - x0 : ix1 - x0]
        m = interior[iy0 - ty0 : iy1 - ty0, ix0 - tx0 : ix1 - tx0]
        sub[m, :3] = np.asarray(self.fill_rgb, np.uint8)
        sub[m, 3] = 255
        _over(out, rgba)
        return (x0, y0, w, h), out

    # -- body-only views ----------------------------------------------------
    def body_region(self, rect: tuple[int, int, int, int]) -> np.ndarray:
        """Body layer output (fill-colour opening applied) for ``rect`` only."""

        x, y, w, h = rect
        out = self.body_canvas[y : y + h, x : x + w].copy()
        mask = self._last_mask
        if mask is None:
            return out
        mr = _clip_rect(mask.rect, *self.canvas_size)
        if mr is None:
            return out
        mx0, my0, mw, mh = mr
        keep = mask.keep[my0 - mask.y0 : my0 - mask.y0 + mh, mx0 - mask.x0 : mx0 - mask.x0 + mw]
        # ROI-sized view of ``out`` covering the mask rect intersection
        ix0, iy0 = max(mx0, x), max(my0, y)
        ix1, iy1 = min(mx0 + mw, x + w), min(my0 + mh, y + h)
        if ix1 <= ix0 or iy1 <= iy0:
            return out
        if self.fill_rgb is None:
            k = keep[iy0 - my0 : iy1 - my0, ix0 - mx0 : ix1 - mx0]
            sub = out[iy0 - y : iy1 - y, ix0 - x : ix1 - x]
            sub[:, :, 3] = (sub[:, :, 3].astype(np.float32) * k).astype(np.uint8)
            return out
        pose = self._last_pose
        cover = (
            (1.0 - keep).astype(np.float32) if pose is None else self._fill_cover(keep, mr, pose)
        )
        c = cover[iy0 - my0 : iy1 - my0, ix0 - mx0 : ix1 - mx0]
        self._paint_fill(out[iy0 - y : iy1 - y, ix0 - x : ix1 - x], c, self.fill_rgb)
        return out

    def body_output(self) -> np.ndarray:
        """Body layer alone with the fill-colour opening applied (full
        canvas; prefer ``body_region`` per frame)."""

        out = self.body_canvas.copy()
        mask = self._last_mask
        if mask is None:
            return out
        mr = _clip_rect(mask.rect, *self.canvas_size)
        if mr is None:
            return out
        out[mr[1] : mr[1] + mr[3], mr[0] : mr[0] + mr[2]] = self.body_region(mr)
        return out


__all__ = [
    "INTERIOR_CLOSE_MAX_K",
    "JUNCTION_INSET_MAX_LW",
    "LIGHT_FILL_LUM",
    "STROKE_DELTA",
    "InkFillOpeningComposer",
    "InkOpeningComposer",
    "body_interior_mask",
    "fill_colour",
    "ink_cut",
    "ink_cut_src",
    "ink_stroke_width",
    "junction_fill_cover",
    "junction_weld_mask",
    "stroke_mask",
    "tail_protect_mask",
]
