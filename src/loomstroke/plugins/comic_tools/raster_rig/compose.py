"""Non-destructive deformer + dirty-rect composer — BZ4R spike.

The two source RGBA arrays are read-only inputs. Every frame produces:

* a TAIL PATCH (posed tail pixels, tail-bbox sized) via an inverse map that
  is computed on a coarse grid and upsampled (cheap even for 4K),
* an OPENING MASK patch (local alpha multiplier for the body around the
  root; only when the recipe enables it),

and the composer rewrites only the union of the old/new dirty rects of a
persistent output canvas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .guide import AttachmentGuide
from .recipe import CompositingRecipe, TailPoseData
from .tail_rig import TailRig


@dataclass
class TailPose:
    """The two-handle pose (+ optional detail bend)."""

    root_u: float
    tip: tuple[float, float]
    bend: tuple[float, float] | None = None

    def to_data(self, detail: bool = False) -> TailPoseData:
        return TailPoseData(self.root_u, self.tip, self.bend, detail)

    @classmethod
    def from_data(cls, data: TailPoseData) -> TailPose:
        return cls(data.root_u, data.tip, data.bend)


@dataclass
class TailPatch:
    x0: int
    y0: int
    rgba: np.ndarray  # (h, w, 4) straight alpha, uint8

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return (self.x0, self.y0, self.rgba.shape[1], self.rgba.shape[0])


@dataclass
class MaskPatch:
    x0: int
    y0: int
    keep: np.ndarray  # (h, w) float32 multiplier 0..1 for the body alpha
    #: optional (h, w) float32 0..1 seed for junction painting (e.g. the
    #: opened-stroke footprint BEFORE tail protection, so a fill sweep spans
    #: the whole base); None = derive from ``keep``
    sweep_src: np.ndarray | None = None

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return (self.x0, self.y0, self.keep.shape[1], self.keep.shape[0])


# ── target axis curve ─────────────────────────────────────────────────


def _cubic(p0, c1, c2, p3, count: int) -> np.ndarray:
    ts = np.linspace(0.0, 1.0, count + 1)
    b0 = (1 - ts) ** 3
    b1 = 3 * (1 - ts) ** 2 * ts
    b2 = 3 * (1 - ts) * ts**2
    b3 = ts**3
    xs = b0 * p0[0] + b1 * c1[0] + b2 * c2[0] + b3 * p3[0]
    ys = b0 * p0[1] + b1 * c1[1] + b2 * c2[1] + b3 * p3[1]
    return np.stack([xs, ys], axis=1)


def target_axis(
    base: tuple[float, float],
    normal: tuple[float, float],
    tip: tuple[float, float],
    bend: tuple[float, float] | None,
    kind: str,
    samples: int = 48,
) -> np.ndarray:
    """Posed axis polyline from ``base`` to ``tip``.

    Non-rigid kinds LEAVE the body along the outline normal (rigid start
    tangent = template-like), then turn towards the tip; the optional bend
    (along fraction, across px) pulls the middle sideways. Rigid = straight.
    """

    ax, ay = tip[0] - base[0], tip[1] - base[1]
    length = math.hypot(ax, ay)
    if length < 1e-6:
        length = 1.0
        ax, ay = normal[0], normal[1]
    ux, uy = ax / length, ay / length
    if kind == "rigid":
        return np.asarray([base, tip], dtype=np.float64)
    c1 = (base[0] + normal[0] * length * 0.3, base[1] + normal[1] * length * 0.3)
    along, across = bend if bend is not None else (0.6, 0.0)
    c2 = (
        base[0] + ux * length * along - uy * across,
        base[1] + uy * length * along + ux * across,
    )
    return _cubic(base, c1, c2, tip, samples)


# ── deformer ──────────────────────────────────────────────────────────


def _polyline_frames(pts: np.ndarray):
    seg = np.diff(pts, axis=0)
    seg_len = np.maximum(1e-9, np.hypot(seg[:, 0], seg[:, 1]))
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    tang = seg / seg_len[:, None]
    return seg_len, cum, tang


def _project(gx, gy, pts, seg_len, cum, tang, *, extend_ends: bool = True):
    """Nearest-segment projection: (segment index, s along, r across, dist)."""

    best_d = np.full(gx.shape, np.inf)
    best_k = np.zeros(gx.shape, np.int64)
    best_s = np.zeros(gx.shape)
    best_r = np.zeros(gx.shape)
    last = len(seg_len) - 1
    for k in range(len(seg_len)):
        vx = gx - pts[k, 0]
        vy = gy - pts[k, 1]
        proj = vx * tang[k, 0] + vy * tang[k, 1]
        lo = -np.inf if (extend_ends and k == 0) else 0.0
        hi = np.inf if (extend_ends and k == last) else seg_len[k]
        foot = np.clip(proj, lo, hi)
        fx = pts[k, 0] + tang[k, 0] * foot
        fy = pts[k, 1] + tang[k, 1] * foot
        d = np.hypot(gx - fx, gy - fy)
        take = d < best_d
        best_d = np.where(take, d, best_d)
        best_k = np.where(take, k, best_k)
        best_s = np.where(take, foot, best_s)
        best_r = np.where(take, -vx * tang[k, 1] + vy * tang[k, 0], best_r)
    return best_k, best_s, best_r, best_d


def _remap_rgba_premultiplied(
    premul: np.ndarray, map_x: np.ndarray, map_y: np.ndarray
) -> np.ndarray:
    """Bilinear remap of a premultiplied float RGBA image → straight uint8
    RGBA (transparent pixels get RGB 0 — no colour leaks into alpha=0)."""

    import cv2  # noqa: PLC0415

    out = cv2.remap(
        premul,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0.0, 0.0, 0.0, 0.0),
    )
    a = out[:, :, 3:4]
    safe = np.maximum(a, 1e-3)
    rgb = np.where(a > 0.5, out[:, :, :3] * (255.0 / safe), 0.0)
    result = np.empty(out.shape, np.uint8)
    result[:, :, :3] = np.clip(np.rint(rgb), 0, 255)
    result[:, :, 3] = np.clip(np.rint(a[:, :, 0]), 0, 255)
    return result


def analyze_inverse_map(
    map_x: np.ndarray, map_y: np.ndarray, sampled_alpha: np.ndarray
) -> dict[str, float]:
    """Deformation-quality numbers for one inverse map (dest → source):

    * ``fold_over_ratio``: share of covered pixels whose Jacobian
      determinant is <= 0 (orientation flip = fold / self-overlap)
    * ``jacobian_min`` / ``jacobian_max``: det range on covered pixels
    * ``max_stretch`` / ``min_stretch``: singular-value extremes (source px
      per dest px; < 1 = the tail is stretched, > 1 = compressed)
    * ``nan_count``: non-finite map entries
    """

    valid = np.isfinite(map_x) & np.isfinite(map_y)
    nan_count = int((~valid).sum())
    cov = (sampled_alpha > 8) & valid
    if cov.sum() < 4:
        return {
            "fold_over_ratio": 0.0,
            "jacobian_min": 1.0,
            "jacobian_max": 1.0,
            "max_stretch": 1.0,
            "min_stretch": 1.0,
            "nan_count": float(nan_count),
        }
    mx = np.where(valid, map_x, 0.0).astype(np.float64)
    my = np.where(valid, map_y, 0.0).astype(np.float64)
    dxx = np.gradient(mx, axis=1)
    dxy = np.gradient(mx, axis=0)
    dyx = np.gradient(my, axis=1)
    dyy = np.gradient(my, axis=0)
    det = dxx * dyy - dxy * dyx
    # singular values of the 2x2 Jacobian
    a2 = dxx**2 + dxy**2 + dyx**2 + dyy**2
    disc = np.sqrt(np.maximum(0.0, a2**2 - 4.0 * det**2))
    smax = np.sqrt(np.maximum(0.0, (a2 + disc) * 0.5))
    smin = np.sqrt(np.maximum(0.0, (a2 - disc) * 0.5))
    d = det[cov]
    return {
        "fold_over_ratio": float((d <= 0).sum() / max(1, d.size)),
        "jacobian_min": float(d.min()),
        "jacobian_max": float(d.max()),
        "max_stretch": float(smax[cov].max()),
        "min_stretch": float(smin[cov].min()),
        "nan_count": float(nan_count),
    }


class TailDeformer:
    """Maps the posed tail back to the pristine tail image (inverse warp).

    Rest side: the tail's spine (M points, tail-local). Posed side: the
    spine points carried through the target axis curve (their along/across
    coordinates relative to the rest chord are re-expressed on the posed
    axis, so a drawn curvature is kept). Pixels are mapped per bone with an
    axis-only stretch; the base band (``rigid_fraction``) is mapped by the
    first bone alone so the joint never distorts.
    """

    def __init__(self, tail_rgba: np.ndarray, rig: TailRig, guide: AttachmentGuide) -> None:
        self.tail = tail_rgba
        # geometry in RIG space (source x scale); the inverse map is divided
        # by the scale at the very end so the PRISTINE source is resampled
        # exactly once per frame whatever the scale
        self.source_rig = rig
        self.scale = TailRig.validate_scale(rig.source_to_rig_scale)
        rig = rig.rig_space()
        self.rig = rig
        self.guide = guide
        # premultiplied float copy of the PRISTINE tail (built once); every
        # frame resamples THIS, never a previous output
        a = tail_rgba[:, :, 3:4].astype(np.float32) / 255.0
        self._premul = np.concatenate(
            [tail_rgba[:, :, :3].astype(np.float32) * a, a * 255.0], axis=2
        ).astype(np.float32)
        # minification (scale < 1): bilinear remap alone under-samples thin
        # strokes, so the sampling image is an area-filtered copy of the
        # PRISTINE premultiplied source at rig resolution — a derived
        # cache rebuilt from the source for this scale (never from an
        # output, never persisted). Magnification samples the source.
        self._sample = self._premul
        self._sample_fx = 1.0  # source px -> sample px
        self._sample_fy = 1.0
        if self.scale < 1.0:
            import cv2  # noqa: PLC0415

            th, tw = tail_rgba.shape[:2]
            sw, sh = max(1, math.ceil(tw * self.scale)), max(1, math.ceil(th * self.scale))
            self._sample = cv2.resize(self._premul, (sw, sh), interpolation=cv2.INTER_AREA)
            self._sample_fx = sw / tw
            self._sample_fy = sh / th
        self.last_map: tuple[np.ndarray, np.ndarray, int, int] | None = None
        # rest chord frame
        (ux, uy), length = rig.rest_axis
        self.rest_u = (ux, uy)
        self.rest_v = (-uy, ux)
        self.rest_len = max(1e-6, length)
        # rest spine resampled to bones (dense for smooth/skeleton, corners
        # only for corner_preserve, single bone for rigid)
        spine = np.asarray(rig.spine, dtype=np.float64)
        if rig.deform_kind == "rigid":
            spine = spine[[0, -1]]
        elif rig.deform_kind in ("smooth", "skeleton"):
            spine = _resample_open(spine, 16)
        self.rest_spine = spine
        self.rest_seg, self.rest_cum, self.rest_tang = _polyline_frames(spine)
        # chord coords of every rest spine point
        bx, by = rig.base_centre
        dx = spine[:, 0] - bx
        dy = spine[:, 1] - by
        self.rest_s = dx * ux + dy * uy  # along the rest chord
        self.rest_r = -dx * uy + dy * ux  # across
        # extent of the ink across the rest axis (for the dest bbox)
        x0, y0, w, h = rig.bbox
        corners = np.array([[x0, y0], [x0 + w, y0], [x0, y0 + h], [x0 + w, y0 + h]], dtype=float)
        rr = -(corners[:, 0] - bx) * uy + (corners[:, 1] - by) * ux
        ss = (corners[:, 0] - bx) * ux + (corners[:, 1] - by) * uy
        self.r_extent = float(np.abs(rr).max()) + 3.0
        self.s_min = float(ss.min())
        self.s_max = float(ss.max())

    # posed spine for a pose --------------------------------------------
    def posed_spine(
        self, pose: TailPose, *, recipe: CompositingRecipe
    ) -> tuple[np.ndarray, np.ndarray]:
        """(posed spine points, posed axis polyline) in PAGE coordinates."""

        p, _t, n = self.guide.frame_at(pose.root_u)
        gx, gy = self.guide.origin
        base = (p[0] + gx - n[0] * recipe.overlap_px, p[1] + gy - n[1] * recipe.overlap_px)
        axis = target_axis(base, n, pose.tip, pose.bend, self.rig.deform_kind)
        seg_len, cum, tang = _polyline_frames(axis)
        total = float(cum[-1])
        scale = total / self.rest_len
        # map rest (s, r) → axis point at s*scale + normal*r
        s_target = np.clip(self.rest_s * scale, 0.0, total)
        idx = np.clip(np.searchsorted(cum, s_target, side="right") - 1, 0, len(seg_len) - 1)
        frac = (s_target - cum[idx]) / seg_len[idx]
        px = axis[idx, 0] + tang[idx, 0] * frac * seg_len[idx]
        py = axis[idx, 1] + tang[idx, 1] * frac * seg_len[idx]
        nx, ny = -tang[idx, 1], tang[idx, 0]
        posed = np.stack([px + nx * self.rest_r, py + ny * self.rest_r], axis=1)
        # the base point itself is exact (rest_s = 0, rest_r = 0)
        posed[0] = base
        return posed, axis

    def posed_base_endpoints(
        self, pose: TailPose, *, recipe: CompositingRecipe
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        """Page positions of the tail's base_left / base_right under the
        (pure rigid) base transform of ``pose`` — where the drawn base
        endpoints land in the composite."""

        posed, _axis = self.posed_spine(pose, recipe=recipe)
        p_seg, _cum, p_tang = _polyline_frames(posed)
        del p_seg
        rig = self.rig
        out = []
        for p in (rig.base_left, rig.base_right):
            dx = p[0] - self.rest_spine[0, 0]
            dy = p[1] - self.rest_spine[0, 1]
            s0 = dx * self.rest_tang[0, 0] + dy * self.rest_tang[0, 1]
            r0 = -dx * self.rest_tang[0, 1] + dy * self.rest_tang[0, 0]
            out.append(
                (
                    float(posed[0, 0] + p_tang[0, 0] * s0 - p_tang[0, 1] * r0),
                    float(posed[0, 1] + p_tang[0, 1] * s0 + p_tang[0, 0] * r0),
                )
            )
        return out[0], out[1]

    def render(  # noqa: PLR0915 - one inverse-map pipeline
        self,
        pose: TailPose,
        *,
        recipe: CompositingRecipe,
        coarse: int = 4,
        coarse_min_px: int = 40_000,
    ) -> TailPatch | None:
        posed, _axis = self.posed_spine(pose, recipe=recipe)
        p_seg, p_cum, p_tang = _polyline_frames(posed)
        # dest bbox: posed spine bbox ± across extent, plus base/tip overhang
        margin = self.r_extent + max(0.0, -self.s_min) + 4.0
        x0 = math.floor(posed[:, 0].min() - margin)
        y0 = math.floor(posed[:, 1].min() - margin)
        x1 = math.ceil(posed[:, 0].max() + margin)
        y1 = math.ceil(posed[:, 1].max() + margin)
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0 or w * h > 12_000_000:
            return None
        step = coarse if (w * h) > coarse_min_px else 1
        gw = -(-w // step) + 1
        gh = -(-h // step) + 1
        gy, gx = np.mgrid[0:gh, 0:gw].astype(np.float64)
        gx = gx * step + x0
        gy = gy * step + y0
        k, s, r, _d = _project(gx, gy, posed, p_seg, p_cum, p_tang)
        # rest position on bone k: axis-only stretch per bone
        ratio = self.rest_seg[k] / p_seg[k]
        s_rest = s * ratio
        rx = self.rest_spine[k, 0] + self.rest_tang[k, 0] * s_rest - self.rest_tang[k, 1] * r
        ry = self.rest_spine[k, 1] + self.rest_tang[k, 1] * s_rest + self.rest_tang[k, 0] * r
        # rigid base band: pixels whose rest chord-s is small are mapped by
        # bone 0 alone (blend over a short margin)
        if self.rig.rigid_fraction > 0 and len(p_seg) > 1:
            band = self.rig.rigid_fraction * self.rest_len
            marg = max(3.0, 0.5 * band)
            # bone-0 mapping for every pixel
            vx = gx - posed[0, 0]
            vy = gy - posed[0, 1]
            s0 = vx * p_tang[0, 0] + vy * p_tang[0, 1]
            r0 = -vx * p_tang[0, 1] + vy * p_tang[0, 0]
            # the base band is a PURE rigid transform (rotate + translate,
            # NO axis stretch): the root-side pixels of the tail keep their
            # drawn shape exactly; the stretch is absorbed beyond the band
            # by the blend zone
            rx0 = self.rest_spine[0, 0] + self.rest_tang[0, 0] * s0 - self.rest_tang[0, 1] * r0
            ry0 = self.rest_spine[0, 1] + self.rest_tang[0, 1] * s0 + self.rest_tang[0, 0] * r0
            # rest chord-s of the bone-0 mapped point decides the blend
            bx, by = self.rig.base_centre
            s_chord = (rx0 - bx) * self.rest_u[0] + (ry0 - by) * self.rest_u[1]
            wgt = np.clip((band + marg - s_chord) / (2.0 * marg), 0.0, 1.0)
            rx = wgt * rx0 + (1.0 - wgt) * rx
            ry = wgt * ry0 + (1.0 - wgt) * ry
        if self.scale != 1.0:
            # rig px -> source px (scale anchored at the image origin in
            # rig-local coordinates; the page anchor lives in rig.origin)
            rx = rx / self.scale
            ry = ry / self.scale
        map_x = rx.astype(np.float32)
        map_y = ry.astype(np.float32)
        if step > 1:
            from .grid_sampling import expand_grid  # noqa: PLC0415

            map_x = expand_grid(map_x, w, h, step)
            map_y = expand_grid(map_y, w, h, step)
        else:
            map_x = map_x[:h, :w]
            map_y = map_y[:h, :w]
        if self._sample_fx != 1.0 or self._sample_fy != 1.0:
            # last_map stays in SOURCE px; the remap reads the area-filtered
            # sample image (sample px = source px * fx, fx ~= scale)
            # pixel-centre convention: source index p -> sample index
            # (p + 0.5) * fx - 0.5 (what cv2.resize used to build the sample)
            fx = np.float32(self._sample_fx)
            # Integer sample dimensions can round differently on each axis.
            fy = np.float32(self._sample_fy)
            half = np.float32(0.5)
            patch = _remap_rgba_premultiplied(
                self._sample, (map_x + half) * fx - half, (map_y + half) * fy - half
            )
        else:
            patch = _remap_rgba_premultiplied(self._sample, map_x, map_y)
        self.last_map = (map_x, map_y, x0, y0)
        if recipe.base_hide_px > 0.5:
            # fade the tail's own base rows (rest chord-s below the hide px)
            bx, by = self.rig.base_centre
            s_chord = (map_x * self.scale - bx) * self.rest_u[0] + (
                map_y * self.scale - by
            ) * self.rest_u[1]
            fade = np.clip(s_chord / recipe.base_hide_px, 0.0, 1.0)
            patch[:, :, 3] = (patch[:, :, 3].astype(np.float32) * fade).astype(np.uint8)
        return TailPatch(x0, y0, np.ascontiguousarray(patch))


def _resample_open(pts: np.ndarray, count: int) -> np.ndarray:
    seg_len, cum, _t = _polyline_frames(pts)
    total = float(cum[-1])
    if total <= 1e-9 or len(pts) < 2:
        return pts
    targets = np.linspace(0.0, total, count + 1)
    idx = np.clip(np.searchsorted(cum, targets, side="right") - 1, 0, len(seg_len) - 1)
    frac = (targets - cum[idx]) / seg_len[idx]
    out = pts[idx] + (pts[idx + 1] - pts[idx]) * frac[:, None]
    out[-1] = pts[-1]
    return out


# ── opening mask ──────────────────────────────────────────────────────


def _band_depth_px(guide: AttachmentGuide, u0: float, u1: float, ink_width: float | None) -> float:
    """Band depth (px, each side of the guide) needed to cut through the
    outline over the arc [u0, u1]. ``ink_width`` None = the original
    ``excursion + line_width / 2`` (bit-identical path). Otherwise the RAW
    outline excursion (the stored per-vertex excursion already carries
    ``line_width / 2``) plus the measured outline-stroke width."""

    exc = guide.excursion_over(u0, u1)
    if ink_width is None:
        return exc + guide.line_width * 0.5
    return max(0.0, exc - guide.line_width * 0.5) + float(ink_width)


def _band_inside(
    gx: np.ndarray,
    gy: np.ndarray,
    pts: np.ndarray,
    depth: float,
    feather: float,
    end_fade_px: float | None,
) -> np.ndarray:
    """Band membership 0..1 of the grid points: across the arc polyline a
    ``depth`` band with ``feather`` edges; along the arc the ends fade over
    2 x feather (``end_fade_px`` None, the original), are HARD (<= 0) or
    fade over ``end_fade_px``. Explicit-end callers also pick the nearest
    UN-extended segment — a point well inside a bending arc must not be
    claimed by an end segment's extension and then be dropped as "beyond
    the end" — measuring the along position on that segment's line."""

    seg_len, cum, tang = _polyline_frames(pts)
    total = float(cum[-1])
    if end_fade_px is None:
        k, s, r, _d = _project(gx, gy, pts, seg_len, cum, tang, extend_ends=True)
        across = np.abs(r)
        # distance beyond the arc ends (CUMULATIVE arc position — ``s`` is
        # per-segment)
        s_cum = cum[k] + s
        beyond = np.maximum(0.0, np.maximum(-s_cum, s_cum - total))
    else:
        # explicit-end callers: the along test is the UNION of the segment
        # projection above and a projection onto the arc's CHORD. A point
        # deep inside a sharply bent arc (the root on a body corner / bump)
        # is "beyond the end" of every segment line but well within the
        # chord, so the deep cut through a thick outline reaches it there
        k, s, r, _d = _project(gx, gy, pts, seg_len, cum, tang, extend_ends=True)
        across = np.abs(r)
        s_cum = cum[k] + s
        beyond_seg = np.maximum(0.0, np.maximum(-s_cum, s_cum - total))
        cx, cy = float(pts[-1, 0] - pts[0, 0]), float(pts[-1, 1] - pts[0, 1])
        chord = max(1e-9, math.hypot(cx, cy))
        mx, my = float(pts[0, 0] + pts[-1, 0]) * 0.5, float(pts[0, 1] + pts[-1, 1]) * 0.5
        s_c = ((gx - mx) * cx + (gy - my) * cy) / chord
        beyond_chord = np.maximum(0.0, np.abs(s_c) - chord * 0.5)
        beyond = np.minimum(beyond_seg, beyond_chord)
    inside = np.clip((depth + feather - across) / feather, 0.0, 1.0)
    if end_fade_px is None:
        end_fade = feather * 2
        inside *= np.clip((end_fade - beyond) / end_fade, 0.0, 1.0)
    elif float(end_fade_px) <= 0.0:
        inside *= (beyond <= 0.0).astype(np.float32)
    else:
        end_fade = float(end_fade_px)
        inside *= np.clip((end_fade - beyond) / end_fade, 0.0, 1.0)
    return inside


def opening_mask(
    guide: AttachmentGuide,
    rig: TailRig,
    pose: TailPose,
    recipe: CompositingRecipe,
    *,
    coarse: int = 1,
    ink_width: float | None = None,
    end_fade_px: float | None = None,
    roi_pad_px: float = 0.0,
) -> MaskPatch | None:
    """Local alpha multiplier (page coords) that opens the body outline
    around the root: 0 inside a band of ``opening_depth_px`` across the
    outline for the arc [u - half, u + half], soft ends/edges.

    ``coarse`` > 1 evaluates the band on a grid of that step and upsamples
    (bilinear) to full resolution — the INTERACTIVE preview quality. Same
    geometry, same meaning; only the sampling density differs. ``coarse``
    = 1 is the exact full-resolution (settle) computation.

    ``ink_width`` (optional) is the measured width of the body's OUTLINE
    STROKE; when given, the band depth covers the raw outline excursion
    plus that stroke width instead of ``guide.line_width`` (which, for a
    FILLED body, is the width of the whole shape and would cut through to
    the far side). ``None`` keeps the original computation bit-identical."""

    if not recipe.opening_enabled:
        return None
    rig = rig.rig_space()
    half_px = rig.base_width * 0.5 + recipe.opening_extra_px
    half_u = half_px / max(1e-9, guide.perimeter)
    n = max(4, int(half_px * 2 / 4.0))
    us = [(pose.root_u - half_u + 2 * half_u * i / n) for i in range(n + 1)]
    pts = np.asarray([guide.point_at(u) for u in us], dtype=np.float64)
    pts[:, 0] += guide.origin[0]
    pts[:, 1] += guide.origin[1]
    # the band must cover the outline's local excursion (spikes/bumps swing
    # around the smoothed guide) — measured at registration, not a style
    depth = max(
        recipe.opening_depth_px,
        _band_depth_px(guide, pose.root_u - half_u, pose.root_u + half_u, ink_width),
    )
    feather = max(0.5, recipe.opening_feather_px)
    # ``roi_pad_px`` only enlarges the allocated rect (room for callers that
    # paint inside the band ROI, e.g. the junction fill sweep); the cut
    # geometry itself is unchanged
    pad = math.ceil(depth + feather + 2 + max(0.0, float(roi_pad_px)))
    x0 = math.floor(pts[:, 0].min()) - pad
    y0 = math.floor(pts[:, 1].min()) - pad
    x1 = math.ceil(pts[:, 0].max()) + pad + 1
    y1 = math.ceil(pts[:, 1].max()) + pad + 1
    w, h = x1 - x0, y1 - y0
    step = max(1, int(coarse)) if (w * h) > 4096 else 1
    if step > 1:
        gw = -(-w // step) + 1
        gh = -(-h // step) + 1
        gy, gx = np.mgrid[0:gh, 0:gw].astype(np.float32)
        gx = gx * step + x0
        gy = gy * step + y0
    else:
        gy, gx = np.mgrid[0:h, 0:w].astype(np.float32)
        gx += x0
        gy += y0
    inside = _band_inside(gx, gy, pts, depth, feather, end_fade_px)
    keep = (1.0 - inside).astype(np.float32)
    if step > 1:
        from .grid_sampling import expand_grid  # noqa: PLC0415

        keep = expand_grid(keep, w, h, step)
    return MaskPatch(x0, y0, keep)


# ── composer ──────────────────────────────────────────────────────────


def _rect_union(*rects):
    rects = [r for r in rects if r is not None]
    if not rects:
        return None
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return (x0, y0, x1 - x0, y1 - y0)


def _clip_rect(rect, width: int, height: int):
    if rect is None:
        return None
    x0, y0, w, h = rect
    x1, y1 = x0 + w, y0 + h
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(height, y1)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def _over(dst: np.ndarray, src: np.ndarray) -> None:
    """Straight-alpha source-over, in place on ``dst`` (uint8 RGBA). Only
    the pixels where ``src`` has coverage are touched."""

    sel = src[:, :, 3] > 0
    if not sel.any():
        return
    s = src[sel].astype(np.float32)
    d = dst[sel].astype(np.float32)
    sa = s[:, 3:4] / 255.0
    da = d[:, 3:4] / 255.0
    oa = sa + da * (1.0 - sa)
    safe = np.maximum(oa, 1e-6)
    rgb = (s[:, :3] * sa + d[:, :3] * da * (1.0 - sa)) / safe
    out = np.empty_like(d)
    out[:, :3] = np.clip(np.rint(rgb), 0, 255)
    out[:, 3] = np.clip(np.rint(oa[:, 0] * 255.0), 0, 255)
    dst[sel] = out.astype(np.uint8)


@dataclass
class RigComposer:
    """Persistent output canvas updated by dirty rects.

    ``body_rgba`` / ``tail_rgba`` are never written. ``body_canvas`` is the
    pristine body placed on the canvas (built once); ``output`` is the
    composed result. ``update(pose)`` rewrites only the union of the old
    and new dirty rects (old tail bounds + new tail bounds + opening bands).
    """

    body_rgba: np.ndarray
    tail_rgba: np.ndarray
    guide: AttachmentGuide
    rig: TailRig
    recipe: CompositingRecipe
    canvas_size: tuple[int, int]
    output: np.ndarray = field(init=False)
    body_canvas: np.ndarray = field(init=False)
    _deformer: TailDeformer = field(init=False)
    _last_rect: tuple[int, int, int, int] | None = field(default=None, init=False)
    _last_pose: TailPose | None = field(default=None, init=False)
    _last_tail: TailPatch | None = field(default=None, init=False)
    _last_mask: MaskPatch | None = field(default=None, init=False)
    stats: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        w, h = self.canvas_size
        self.body_canvas = np.zeros((h, w, 4), np.uint8)
        bx, by = round(self.guide.origin[0]), round(self.guide.origin[1])
        bh, bw = self.body_rgba.shape[:2]
        rect = _clip_rect((bx, by, bw, bh), w, h)
        if rect is not None:
            x0, y0, rw, rh = rect
            self.body_canvas[y0 : y0 + rh, x0 : x0 + rw] = self.body_rgba[
                y0 - by : y0 - by + rh, x0 - bx : x0 - bx + rw
            ]
        self.output = self.body_canvas.copy()
        self._deformer = TailDeformer(self.tail_rgba, self.rig, self.guide)

    # -- per-frame update ---------------------------------------------
    def update(
        self, pose: TailPose, *, opening: bool | None = None
    ) -> tuple[int, int, int, int] | None:
        recipe = self.recipe
        if opening is not None and opening != recipe.opening_enabled:
            from dataclasses import replace as _replace  # noqa: PLC0415

            recipe = _replace(recipe, opening_enabled=opening)
        tail = self._deformer.render(pose, recipe=recipe, coarse_min_px=self.render_coarse_min_px())
        tail = self._clip_tail(tail, pose, recipe)
        mask = self._opening_mask(pose, recipe, tail)
        new_rect = _rect_union(tail.rect if tail else None, mask.rect if mask else None)
        dirty = _clip_rect(_rect_union(self._last_rect, new_rect), *self.canvas_size)
        if dirty is None:
            self._last_rect, self._last_pose, self._last_tail, self._last_mask = (
                new_rect,
                pose,
                tail,
                mask,
            )
            return None
        x0, y0, w, h = dirty
        region = self.output[y0 : y0 + h, x0 : x0 + w]
        # 1) restore the pristine body in the dirty rect
        region[...] = self.body_canvas[y0 : y0 + h, x0 : x0 + w]
        # 2) opening: multiply the body alpha locally
        if mask is not None:
            mr = _clip_rect(mask.rect, *self.canvas_size)
            if mr is not None:
                mx0, my0, mw, mh = mr
                keep = mask.keep[
                    my0 - mask.y0 : my0 - mask.y0 + mh, mx0 - mask.x0 : mx0 - mask.x0 + mw
                ]
                sub = self.output[my0 : my0 + mh, mx0 : mx0 + mw]
                sub[:, :, 3] = (sub[:, :, 3].astype(np.float32) * keep).astype(np.uint8)
        # 3) tail over/under
        if tail is not None:
            tr = _clip_rect(tail.rect, *self.canvas_size)
            if tr is not None:
                tx0, ty0, tw, th = tr
                patch = tail.rgba[
                    ty0 - tail.y0 : ty0 - tail.y0 + th, tx0 - tail.x0 : tx0 - tail.x0 + tw
                ]
                dst = self.output[ty0 : ty0 + th, tx0 : tx0 + tw]
                if recipe.z_order == "tail_under":
                    body_part = dst.copy()
                    dst[...] = patch
                    _over(dst, body_part)
                else:
                    _over(dst, patch)
        self._last_rect, self._last_pose, self._last_tail, self._last_mask = (
            new_rect,
            pose,
            tail,
            mask,
        )
        self._post_compose(pose, recipe, tail)
        return dirty

    def _clip_tail(
        self,
        tail: TailPatch | None,
        pose: TailPose,  # noqa: ARG002 - hook signature
        recipe: CompositingRecipe,  # noqa: ARG002 - hook signature
    ) -> TailPatch | None:
        """Hook applied to the posed tail patch before compositing
        (overridable, e.g. to keep tail ink out of the body interior).
        Default: the patch unchanged."""

        return tail

    def _post_compose(
        self, pose: TailPose, recipe: CompositingRecipe, tail: TailPatch | None
    ) -> None:
        """Hook after tail compositing (overridable by evaluation
        prototypes, e.g. junction end bridging). Default: nothing."""

    def _opening_mask(
        self, pose: TailPose, recipe: CompositingRecipe, tail: TailPatch | None  # noqa: ARG002
    ) -> MaskPatch | None:
        """Opening mask hook (overridable by evaluation prototypes)."""

        return self._band_mask(pose, recipe)

    def _band_mask(
        self,
        pose: TailPose,
        recipe: CompositingRecipe,
        *,
        arc: tuple[float, float] | None = None,
        ink_width: float | None = None,
        end_fade_px: float | None = None,
        roi_pad_px: float = 0.0,
    ) -> MaskPatch | None:
        """``opening_mask`` with an EXACT cache keyed on the band inputs.
        ``arc`` overrides (centre_u, span_px) — used by prototypes that cut
        exactly between the POSED tail legs instead of the registered base
        width. ``ink_width`` / ``end_fade_px`` / ``roi_pad_px`` are forwarded
        to ``opening_mask`` (band depth from the measured outline-stroke
        width; along-arc end fade; extra ROI room; None / 0 = original)."""

        centre_u = pose.root_u if arc is None else arc[0]
        span_px = None if arc is None else arc[1]
        coarse = self.band_coarse(self._band_roi_px(pose, recipe, arc, ink_width, roi_pad_px))
        key = (
            centre_u,
            span_px,
            recipe.opening_enabled,
            recipe.opening_extra_px,
            recipe.opening_depth_px,
            recipe.opening_feather_px,
            coarse,
            ink_width,
            end_fade_px,
            roi_pad_px,
        )
        cached = getattr(self, "_band_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        rig = self.rig
        if span_px is not None:
            from dataclasses import replace as _replace  # noqa: PLC0415

            rig = _replace(
                rig.rig_space() if rig.source_to_rig_scale != 1.0 else rig,
                base_width=float(span_px),
                source_to_rig_scale=1.0,
            )
        mask = opening_mask(
            self.guide,
            rig,
            TailPose(centre_u, pose.tip),
            recipe,
            coarse=coarse,
            ink_width=ink_width,
            end_fade_px=end_fade_px,
            roi_pad_px=roi_pad_px,
        )
        self._band_cache = (key, mask)
        return mask

    #: preview quality: "settle" = exact full-resolution band (default,
    #: the evaluated behaviour); "interactive" = coarse-grid band during a
    #: drag (same geometry, lower sampling density)
    preview_quality: str = "settle"
    INTERACTIVE_BAND_STEP = 4

    #: settle (full quality) band grid: exact full-resolution up to this ROI
    #: area; larger bands use step 2 / 3 (same geometry, sub-pixel feather
    #: sampling only) so a release never stalls for hundreds of ms
    SETTLE_EXACT_MAX_PX = 120_000

    def band_coarse(self, roi_px: int | None = None) -> int:
        if self.preview_quality == "interactive":
            return self.INTERACTIVE_BAND_STEP
        if roi_px is None or roi_px <= self.SETTLE_EXACT_MAX_PX:
            return 1
        return 2 if roi_px <= 4 * self.SETTLE_EXACT_MAX_PX else 3

    def _band_roi_px(
        self,
        pose: TailPose,
        recipe: CompositingRecipe,
        arc: tuple[float, float] | None,
        ink_width: float | None = None,
        roi_pad_px: float = 0.0,
    ) -> int:
        """Cheap estimate of the band ROI area (the rect ``opening_mask``
        will allocate) used to pick the settle grid step."""

        rig = self.rig.rig_space()
        half_px = (rig.base_width if arc is None else arc[1]) * 0.5 + recipe.opening_extra_px
        guide = self.guide
        half_u = half_px / max(1e-9, guide.perimeter)
        centre_u = pose.root_u if arc is None else arc[0]
        n = max(4, int(half_px * 2 / 4.0))
        us = [(centre_u - half_u + 2 * half_u * i / n) for i in range(n + 1)]
        pts = np.asarray([guide.point_at(u) for u in us], dtype=np.float64)
        depth = max(
            recipe.opening_depth_px,
            _band_depth_px(guide, centre_u - half_u, centre_u + half_u, ink_width),
        )
        pad = math.ceil(
            depth + max(0.5, recipe.opening_feather_px) + 2 + max(0.0, float(roi_pad_px))
        )
        w = math.ceil(pts[:, 0].max()) - math.floor(pts[:, 0].min()) + 2 * pad + 1
        h = math.ceil(pts[:, 1].max()) - math.floor(pts[:, 1].min()) + 2 * pad + 1
        return int(max(1, w) * max(1, h))

    def render_coarse_min_px(self) -> int:
        """Tail inverse-map grid: interactive previews use the coarse grid
        for any patch above 4k px (settle keeps the 40k px default)."""

        return 4_000 if self.preview_quality == "interactive" else 40_000

    def body_region(self, rect: tuple[int, int, int, int]) -> np.ndarray:
        """Body layer output (opening applied) for ``rect`` only — the
        cheap per-frame variant of ``body_output`` (no full-canvas copy)."""

        x, y, w, h = rect
        out = self.body_canvas[y : y + h, x : x + w].copy()
        mask = self._last_mask
        if mask is None:
            return out
        mr = _clip_rect(mask.rect, *self.canvas_size)
        if mr is None:
            return out
        ix0, iy0 = max(mr[0], x), max(mr[1], y)
        ix1, iy1 = min(mr[0] + mr[2], x + w), min(mr[1] + mr[3], y + h)
        if ix1 <= ix0 or iy1 <= iy0:
            return out
        keep = mask.keep[iy0 - mask.y0 : iy1 - mask.y0, ix0 - mask.x0 : ix1 - mask.x0]
        sub = out[iy0 - y : iy1 - y, ix0 - x : ix1 - x]
        sub[:, :, 3] = (sub[:, :, 3].astype(np.float32) * keep).astype(np.uint8)
        return out

    def body_output(self) -> np.ndarray:
        """Body layer output alone (pristine body + current opening mask)."""

        out = self.body_canvas.copy()
        mask = self._last_mask
        if mask is not None:
            mr = _clip_rect(mask.rect, *self.canvas_size)
            if mr is not None:
                mx0, my0, mw, mh = mr
                keep = mask.keep[
                    my0 - mask.y0 : my0 - mask.y0 + mh, mx0 - mask.x0 : mx0 - mask.x0 + mw
                ]
                sub = out[my0 : my0 + mh, mx0 : mx0 + mw]
                sub[:, :, 3] = (sub[:, :, 3].astype(np.float32) * keep).astype(np.uint8)
        return out

    def opening_rect(self) -> tuple[int, int, int, int] | None:
        return _clip_rect(self._last_mask.rect, *self.canvas_size) if self._last_mask else None

    def tail_rect(self) -> tuple[int, int, int, int] | None:
        return _clip_rect(self._last_tail.rect, *self.canvas_size) if self._last_tail else None

    def preview_tail_patch(self) -> tuple[tuple[int, int, int, int], np.ndarray] | None:
        """(canvas rect, straight-alpha RGBA view) the compositor preview
        draws as the tail layer: the posed tail patch of the last update,
        canvas-clipped. Subclasses add what their junction compositing
        paints over the tail (e.g. end bridges)."""

        tail, tr = self._last_tail, self.tail_rect()
        if tail is None or tr is None:
            return None
        tx0, ty0, tw, th = tr
        return tr, tail.rgba[ty0 - tail.y0 : ty0 - tail.y0 + th, tx0 - tail.x0 : tx0 - tail.x0 + tw]

    def reset(self, registered: TailPose | None = None) -> None:  # noqa: ARG002
        """Back to the registration display: the pristine tail at its drawn
        placement (identity, no deformation) over the pristine body, no
        opening — bit-exact with the two source images."""

        w, h = self.canvas_size
        dirty = _clip_rect(_rect_union(self._last_rect, self._identity_rect()), w, h)
        if dirty is not None:
            x0, y0, dw, dh = dirty
            self.output[y0 : y0 + dh, x0 : x0 + dw] = self.body_canvas[y0 : y0 + dh, x0 : x0 + dw]
        tr = _clip_rect(self._identity_rect(), w, h)
        if tr is not None:
            tx0, ty0, tw, th = tr
            ox, oy, _tw, _th = self._identity_rect()
            src = self._identity_tail()
            patch = src[ty0 - oy : ty0 - oy + th, tx0 - ox : tx0 - ox + tw]
            _over(self.output[ty0 : ty0 + th, tx0 : tx0 + tw], patch)
        self._last_rect = self._identity_rect()
        self._last_tail = None
        self._last_mask = None
        self._last_pose = None

    def _identity_rect(self) -> tuple[int, int, int, int]:
        rig = self.rig.rig_space()
        ox, oy = round(rig.origin[0]), round(rig.origin[1])
        th, tw = self._identity_tail().shape[:2]
        return (ox, oy, tw, th)

    def _identity_tail(self) -> np.ndarray:
        """The tail at the REGISTERED scale, undeformed. Scale 1.0: the
        pristine source itself (bit-exact). Otherwise a single resample of
        the pristine source (derived, rebuilt from the source — never from
        a previous output or a stored bitmap)."""

        s = self.rig.source_to_rig_scale
        if s == 1.0:
            return self.tail_rgba
        cached = getattr(self, "_identity_cache", None)
        if cached is not None and cached[0] == s:
            return cached[1]
        import cv2  # noqa: PLC0415

        th, tw = self.tail_rgba.shape[:2]
        w, h = max(1, math.ceil(tw * s)), max(1, math.ceil(th * s))
        a = self.tail_rgba[:, :, 3:4].astype(np.float32) / 255.0
        premul = np.concatenate(
            [self.tail_rgba[:, :, :3].astype(np.float32) * a, a * 255.0], axis=2
        )
        inter = cv2.INTER_AREA if s < 1.0 else cv2.INTER_LINEAR
        out = cv2.resize(premul, (w, h), interpolation=inter)
        alpha = out[:, :, 3:4]
        rgb = np.where(alpha > 1e-3, out[:, :, :3] / np.maximum(alpha, 1e-6) * 255.0, 0.0)
        rgb = np.where(alpha > 1e-3, rgb, 0.0)
        res = np.concatenate([np.clip(rgb, 0, 255), np.clip(alpha, 0, 255)], axis=2)
        res = np.rint(res).astype(np.uint8)
        self._identity_cache = (s, res)
        return res


__all__ = [
    "MaskPatch",
    "RigComposer",
    "TailDeformer",
    "TailPatch",
    "TailPose",
    "analyze_inverse_map",
    "opening_mask",
    "target_axis",
]
