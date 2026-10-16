"""吹き出し化 (balloonize): hand-drawn balloon → procedural proposal.

Pure estimation pipeline (numpy + OpenCV, no Qt): takes ONE hand-drawn
balloon as a coverage mask and derives a structured proposal — body, tail
and text frame are estimated as SEPARATE elements, never one baked picture:

    input mask → main closed region (body+tail blob) → body core
    → tail as the protrusion off the body → inscribed text frame
    → balloon-kind suggestion

The result is a *proposal*: the caller must run a user confirmation step
before generating the final procedural balloon (semi-automatic by design).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from loomstroke.ai_selection.runtime_imports import load_numpy

TAIL_DETECTED = "detected"
TAIL_AMBIGUOUS = "ambiguous"
TAIL_NONE = "none"

MODE_CLEAN = "clean"
#: Hand-drawn preserve: keep the drawn outline, structure only tail/text/
#: speaker. Designed (provenance carries the mode + the source stays on its
#: layer) but not generated in this phase — see BalloonizeProposal.modes.
MODE_PRESERVE = "preserve"

_MIN_BLOB_AREA_PX = 64
_MIN_TAIL_AREA_RATIO = 0.005
_AMBIGUOUS_TAIL_AREA_RATIO = 0.02


class BalloonizeError(ValueError):
    """Raised when no balloon-like region can be extracted."""


@dataclass(frozen=True)
class TailProposal:
    confidence: str = TAIL_NONE  # detected | ambiguous | none
    connect_point: tuple[float, float] = (0.0, 0.0)
    tip: tuple[float, float] = (0.0, 0.0)
    width_px: float = 0.0


@dataclass(frozen=True)
class BalloonizeProposal:
    """Everything the confirmation UI needs, in page coordinates."""

    body_rect: tuple[float, float, float, float]
    kind: str
    kind_candidates: tuple[str, ...]
    tail: TailProposal
    text_frame_rect: tuple[float, float, float, float]
    source_bbox: tuple[float, float, float, float]
    mode: str = MODE_CLEAN
    modes: tuple[str, ...] = (MODE_CLEAN,)  # preserve is design-only for now
    notes: tuple[str, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _cv2() -> Any:
    try:
        import cv2  # noqa: PLC0415

        return cv2
    except Exception as exc:  # pragma: no cover - environment guard
        raise BalloonizeError("OpenCV is required for balloonize") from exc


def mask_from_alpha(rgba: Any) -> Any:
    """Coverage mask (u8 0/255) from an RGBA array."""

    np = load_numpy()
    array = np.asarray(rgba)
    if array.ndim == 3 and array.shape[2] >= 4:
        return ((array[:, :, 3] > 8).astype(np.uint8)) * 255
    if array.ndim == 2:
        return ((array > 8).astype(np.uint8)) * 255
    raise BalloonizeError("unsupported input array shape")


def _largest_component(cv2: Any, np: Any, mask: Any) -> Any:
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        raise BalloonizeError("input contains no drawn pixels")
    areas = stats[1:, cv2.CC_STAT_AREA]
    best = int(np.argmax(areas)) + 1
    if int(areas[best - 1]) < _MIN_BLOB_AREA_PX:
        raise BalloonizeError("drawn region is too small for a balloon")
    return (labels == best).astype(np.uint8) * 255


def _fill_interior(cv2: Any, np: Any, mask: Any) -> Any:
    """Close small stroke gaps, then fill the enclosed interior.

    Hand-drawn balloons are usually OUTLINES: the non-transparent pixels are
    a ring. Flood-filling the outside and inverting turns the ring into the
    full closed region ("主閉領域").
    """

    h, w = mask.shape
    scale = max(3, round(min(h, w) * 0.03) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (scale, scale))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    padded = np.zeros((h + 2, w + 2), dtype=np.uint8)
    padded[1:-1, 1:-1] = closed
    flood = padded.copy()
    ff_mask = np.zeros((h + 4, w + 4), dtype=np.uint8)
    cv2.floodFill(flood, ff_mask, (0, 0), 255)
    interior = cv2.bitwise_not(flood)[1:-1, 1:-1]
    return cv2.bitwise_or(closed, interior)


def _split_body_and_tail(cv2: Any, np: Any, filled: Any) -> tuple[Any, Any]:
    """Morphological opening keeps the body core; the residue is tail-like."""

    area = int(np.count_nonzero(filled))
    radius = max(2, round(math.sqrt(area) * 0.14))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    core = cv2.erode(filled, kernel)
    if not np.count_nonzero(core):
        return filled, np.zeros_like(filled)
    core = _largest_component(cv2, np, core)
    body = cv2.dilate(core, kernel)
    body = cv2.bitwise_and(body, filled)
    residual = cv2.bitwise_and(filled, cv2.bitwise_not(body))
    return body, residual


def _estimate_tail(cv2: Any, np: Any, body: Any, residual: Any) -> tuple[TailProposal, Any]:
    body_area = float(np.count_nonzero(body)) or 1.0
    if not np.count_nonzero(residual):
        return TailProposal(), None
    count, labels, stats, _c = cv2.connectedComponentsWithStats(residual, connectivity=8)
    if count <= 1:
        return TailProposal(), None
    # candidate = largest residual component that touches the body
    ring = cv2.dilate(body, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    best_label = 0
    best_area = 0
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        component = labels == label
        touches = bool(np.count_nonzero(np.logical_and(component, ring > 0)))
        if touches and area > best_area:
            best_area = area
            best_label = label
    if best_label == 0 or best_area < body_area * _MIN_TAIL_AREA_RATIO:
        return TailProposal(), None
    component = (labels == best_label).astype(np.uint8)

    moments = cv2.moments(body, binaryImage=True)
    cx = moments["m10"] / max(1.0, moments["m00"])
    cy = moments["m01"] / max(1.0, moments["m00"])
    ys, xs = np.nonzero(component)
    distances = (xs - cx) ** 2 + (ys - cy) ** 2
    tip_index = int(np.argmax(distances))
    tip = (float(xs[tip_index]), float(ys[tip_index]))

    contact = np.logical_and(component > 0, ring > 0)
    if np.count_nonzero(contact):
        cys, cxs = np.nonzero(contact)
        connect = (float(cxs.mean()), float(cys.mean()))
    else:
        connect = (cx, cy)
    length = math.hypot(tip[0] - connect[0], tip[1] - connect[1])
    width = 2.0 * best_area / max(4.0, length) if length > 4.0 else math.sqrt(best_area)
    confidence = (
        TAIL_AMBIGUOUS if best_area < body_area * _AMBIGUOUS_TAIL_AREA_RATIO else TAIL_DETECTED
    )
    if length < math.sqrt(body_area) * 0.18:
        confidence = TAIL_AMBIGUOUS
    return (
        TailProposal(
            confidence=confidence,
            connect_point=connect,
            tip=tip,
            width_px=float(width),
        ),
        component,
    )


def _suggest_kind(cv2: Any, np: Any, body: Any) -> tuple[str, tuple[str, ...]]:
    contours, _ = cv2.findContours(body, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return "speech_round", ("speech_round", "speech_rect")
    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    perimeter = float(cv2.arcLength(contour, closed=True))
    hull = cv2.convexHull(contour)
    hull_perimeter = float(cv2.arcLength(hull, closed=True))
    roughness = perimeter / max(1.0, hull_perimeter)
    _x, _y, w, h = cv2.boundingRect(contour)
    fill_ratio = area / max(1.0, float(w * h))

    if roughness > 1.35:
        kind = "spiky_balloon"
    elif roughness > 1.12:
        kind = "speech_cloud"
    elif fill_ratio > 0.92:
        kind = "speech_rect"
    else:
        kind = "speech_round"
    ordering = [kind]
    for candidate in ("speech_round", "speech_rect", "speech_cloud", "spiky_balloon"):
        if candidate not in ordering:
            ordering.append(candidate)
    return kind, tuple(ordering)


def _inscribed_text_rect(
    cv2: Any, np: Any, body: Any, *, padding_px: float
) -> tuple[float, float, float, float]:
    """A greedy inscribed rect around the deepest interior point."""

    distance = cv2.distanceTransform((body > 0).astype(np.uint8), cv2.DIST_L2, 3)
    _minv, maxv, _minl, max_loc = cv2.minMaxLoc(distance)
    cx, cy = float(max_loc[0]), float(max_loc[1])
    if maxv <= 2.0:
        raise BalloonizeError("body interior is too thin for a text frame")

    h, w = body.shape
    ys, xs = np.nonzero(body)
    bx0, bx1 = float(xs.min()), float(xs.max())
    by0, by1 = float(ys.min()), float(ys.max())
    aspect = max(0.4, min(2.5, (bx1 - bx0 + 1) / max(1.0, by1 - by0 + 1)))

    def _fits(half_w: float, half_h: float) -> bool:
        x0 = int(max(0, math.floor(cx - half_w)))
        x1 = int(min(w - 1, math.ceil(cx + half_w)))
        y0 = int(max(0, math.floor(cy - half_h)))
        y1 = int(min(h - 1, math.ceil(cy + half_h)))
        if x1 <= x0 or y1 <= y0:
            return False
        region = body[y0 : y1 + 1, x0 : x1 + 1]
        return bool(np.all(region > 0))

    lo, hi = 2.0, float(max(w, h))
    for _ in range(24):
        mid = (lo + hi) * 0.5
        if _fits(mid * aspect, mid):
            lo = mid
        else:
            hi = mid
    half_h = lo
    half_w = lo * aspect
    pad = max(0.0, float(padding_px))
    half_w = max(6.0, half_w - pad)
    half_h = max(6.0, half_h - pad)
    return (cx - half_w, cy - half_h, half_w * 2.0, half_h * 2.0)


def propose_balloon(
    mask: Any,
    *,
    origin: tuple[float, float] = (0.0, 0.0),
    padding_px: float = 12.0,
) -> BalloonizeProposal:
    """Run the full estimation pipeline on ONE hand-drawn balloon mask.

    ``mask`` is a (H, W) u8/bool coverage array in local coordinates;
    ``origin`` translates the result into page coordinates.
    """

    cv2 = _cv2()
    np = load_numpy()
    coverage = np.asarray(mask)
    if coverage.dtype != np.uint8:
        coverage = (coverage > 0).astype(np.uint8) * 255
    else:
        coverage = ((coverage > 127).astype(np.uint8)) * 255

    notes: list[str] = []
    blob = _largest_component(cv2, np, coverage)
    filled = _fill_interior(cv2, np, blob)
    body, residual = _split_body_and_tail(cv2, np, filled)
    tail, tail_component = _estimate_tail(cv2, np, body, residual)
    if tail.confidence == TAIL_NONE:
        notes.append("tail_not_detected")
    elif tail.confidence == TAIL_AMBIGUOUS:
        notes.append("tail_ambiguous")
    # kind is judged on the filled outline WITHOUT the tail: the body/tail
    # opening smooths spikes/lobes away (hiding the shape character), while
    # the raw filled blob would let the tail protrusion skew rect/round.
    kind_mask = filled
    if tail_component is not None:
        kind_mask = cv2.bitwise_and(
            filled, cv2.bitwise_not((tail_component > 0).astype(np.uint8) * 255)
        )
        kind_mask = _largest_component(cv2, np, kind_mask)
    kind, candidates = _suggest_kind(cv2, np, kind_mask)
    text_rect_local = _inscribed_text_rect(cv2, np, body, padding_px=padding_px)

    ys, xs = np.nonzero(body)
    bx0, bx1 = float(xs.min()), float(xs.max())
    by0, by1 = float(ys.min()), float(ys.max())
    fys, fxs = np.nonzero(filled)
    sx0, sx1 = float(fxs.min()), float(fxs.max())
    sy0, sy1 = float(fys.min()), float(fys.max())

    ox, oy = float(origin[0]), float(origin[1])

    def _page_rect(rect: tuple[float, float, float, float]):
        return (rect[0] + ox, rect[1] + oy, rect[2], rect[3])

    def _page_point(point: tuple[float, float]):
        return (point[0] + ox, point[1] + oy)

    return BalloonizeProposal(
        body_rect=_page_rect((bx0, by0, bx1 - bx0 + 1, by1 - by0 + 1)),
        kind=kind,
        kind_candidates=candidates,
        tail=TailProposal(
            confidence=tail.confidence,
            connect_point=_page_point(tail.connect_point),
            tip=_page_point(tail.tip),
            width_px=tail.width_px,
        ),
        text_frame_rect=_page_rect(text_rect_local),
        source_bbox=_page_rect((sx0, sy0, sx1 - sx0 + 1, sy1 - sy0 + 1)),
        notes=tuple(notes),
        diagnostics={
            "bodyAreaPx": int(np.count_nonzero(body)),
            "filledAreaPx": int(np.count_nonzero(filled)),
            "tailAreaPx": int(np.count_nonzero(residual)),
        },
    )


def polygon_mask(
    points: Any,
    *,
    supersample: int = 1,
) -> tuple[Any, tuple[float, float]]:
    """Rasterize a closed polygon (vector-path anchors) into a local mask.

    Vector input is preferred over re-rasterizing the composited layer: the
    path itself is the most stable outline source.
    """

    cv2 = _cv2()
    np = load_numpy()
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[0] < 3:
        raise BalloonizeError("polygon needs at least 3 points")
    x0 = float(pts[:, 0].min())
    y0 = float(pts[:, 1].min())
    w = math.ceil(pts[:, 0].max() - x0) + 3
    h = math.ceil(pts[:, 1].max() - y0) + 3
    local = np.round((pts - [x0 - 1, y0 - 1]) * supersample).astype(np.int32)
    mask = np.zeros((h * supersample, w * supersample), dtype=np.uint8)
    cv2.fillPoly(mask, [local], 255)
    if supersample > 1:
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_AREA)
        mask = ((mask > 127).astype(np.uint8)) * 255
    return mask, (x0 - 1, y0 - 1)


# -- hand-drawn outline extraction (Phase BZ2 Preserve) ---------------------


def extract_hand_drawn_outline(
    mask: Any,
    *,
    origin: tuple[float, float] = (0.0, 0.0),
    fill: bool = True,
) -> tuple[list[tuple[float, float]], tuple[float, float, float, float]]:
    """The main drawn shape as an editable polygon, PRESERVING its character.

    No parametric fitting, no smoothing beyond a perimeter-relative
    Douglas-Peucker reduction: epsilon = clamp(perimeter * 0.35%, 0.75px,
    6px). Relative to the contour perimeter, small balloons keep their
    features (epsilon bottoms out below a pixel) and huge balloons do not
    explode into thousands of anchors (epsilon grows with size).
    Returns (page-space points, page-space bbox).
    """

    cv2 = _cv2()
    np = load_numpy()
    coverage = np.asarray(mask)
    if coverage.dtype != np.uint8:
        coverage = (coverage > 0).astype(np.uint8) * 255
    else:
        coverage = ((coverage > 127).astype(np.uint8)) * 255
    blob = _largest_component(cv2, np, coverage)
    shape = _fill_interior(cv2, np, blob) if fill else blob
    contours, _hierarchy = cv2.findContours(shape, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise BalloonizeError("no outline could be traced")
    contour = max(contours, key=cv2.contourArea)
    perimeter = float(cv2.arcLength(contour, closed=True))
    epsilon = min(6.0, max(0.75, perimeter * 0.0035))
    simplified = cv2.approxPolyDP(contour, epsilon, closed=True)
    if len(simplified) < 3:
        simplified = contour
    ox, oy = float(origin[0]), float(origin[1])
    points = [(float(pt[0][0]) + ox, float(pt[0][1]) + oy) for pt in simplified]
    xs = [px for px, _ in points]
    ys = [py for _, py in points]
    bbox = (min(xs), min(ys), max(1.0, max(xs) - min(xs)), max(1.0, max(ys) - min(ys)))
    return points, bbox


def inscribed_rect_for_mask(
    mask: Any,
    *,
    origin: tuple[float, float] = (0.0, 0.0),
    padding_px: float = 12.0,
) -> tuple[float, float, float, float]:
    """Page-space inscribed text rect of the filled main shape."""

    cv2 = _cv2()
    np = load_numpy()
    coverage = np.asarray(mask)
    if coverage.dtype != np.uint8:
        coverage = (coverage > 0).astype(np.uint8) * 255
    else:
        coverage = ((coverage > 127).astype(np.uint8)) * 255
    blob = _largest_component(cv2, np, coverage)
    filled = _fill_interior(cv2, np, blob)
    rect = _inscribed_text_rect(cv2, np, filled, padding_px=padding_px)
    return (rect[0] + float(origin[0]), rect[1] + float(origin[1]), rect[2], rect[3])


__all__ = [
    "MODE_CLEAN",
    "MODE_PRESERVE",
    "TAIL_AMBIGUOUS",
    "TAIL_DETECTED",
    "TAIL_NONE",
    "BalloonizeError",
    "BalloonizeProposal",
    "TailProposal",
    "extract_hand_drawn_outline",
    "inscribed_rect_for_mask",
    "mask_from_alpha",
    "polygon_mask",
    "propose_balloon",
]
