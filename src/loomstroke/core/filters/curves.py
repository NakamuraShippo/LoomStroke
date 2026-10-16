"""Curves tone adjustment live-filter filter.

Free-form tone curve driven by a list of ``(x, y)`` control points.
The points are interpolated via Fritsch-Carlson monotonic cubic splines
to a 256-entry LUT and applied to the un-premultiplied RGB channels.

Migration note: an earlier revision exposed five fixed-X parameters
(``shadows``/``darks``/``midtones``/``lights``/``highlights``) instead
of an editable point list. :meth:`apply` accepts both shapes so old
``.lstroke`` files keep working — see :func:`_migrate_legacy_offsets`.
"""

from __future__ import annotations

import functools
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from . import BaseFilter, FilterParamSchema

# LRU size: 8 entries is plenty in practice — typically only one Curves
# layer is being interacted with at a time, plus a few in the document.
# Each cached LUT is 256 bytes so total cache is ~2KB.
_LUT_CACHE_SIZE = 8

# Endpoints + one optional anchor are enough to express the identity curve;
# users add additional points by clicking on the curve in the Filters panel.
_DEFAULT_POINTS: list[tuple[int, int]] = [(0, 0), (255, 255)]
_LEGACY_KEYS: tuple[str, ...] = ("shadows", "darks", "midtones", "lights", "highlights")
_LEGACY_XS: tuple[int, ...] = (0, 64, 128, 192, 255)


@functools.lru_cache(maxsize=_LUT_CACHE_SIZE)
def _cached_build_lut(points_tuple: tuple[tuple[int, int], ...]) -> NDArray[Any]:
    """LRU-cached LUT builder. ``points_tuple`` MUST be already normalized
    (sorted, deduped, X=0 and X=255 anchored) — callers normalise once and
    pass the immutable tuple form so the cache key is stable.

    The returned array is *shared* across cache hits; callers must NOT
    mutate it. The current code paths (``CurvesFilter.apply``,
    ``CurvesGraphWidget._recompute_lut``, paint code) only ever index
    into the LUT, never write to it.
    """
    xs = np.array([p[0] for p in points_tuple], dtype=np.float64)
    ys = np.array([p[1] for p in points_tuple], dtype=np.float64)
    x_all = np.arange(256, dtype=np.float64)
    result = _monotone_cubic_interp(xs, ys, x_all)
    return np.clip(result, 0, 255).astype(np.uint8)


class CurvesFilter(BaseFilter):
    """Free-form tone curve adjustment driven by editable control points."""

    def name(self) -> str:
        return "Curves"

    def category(self) -> str:
        return "adjustment"

    def param_schema(self) -> list[FilterParamSchema]:
        return [
            FilterParamSchema(
                key="points",
                label="Curve",
                param_type="curve_points",
                default=list(_DEFAULT_POINTS),
            ),
        ]

    def apply(
        self,
        source: QImage,
        params: dict[str, Any],
        mask: QImage | None = None,
    ) -> QImage:
        # Single normalize per call — both the identity short-circuit and
        # the LUT build want the same canonical point list.
        points = _resolve_points(params)
        normalized = _normalize_points(points)

        # Identity curve → no work needed; return a defensive copy.
        if all(x == y for x, y in normalized):
            return source.copy()

        src = source.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        w, h = src.width(), src.height()
        if w == 0 or h == 0:
            return src.copy()

        byte_count = src.bytesPerLine() * h
        buf = src.bits()
        if hasattr(buf, "setsize"):
            buf.setsize(byte_count)
        arr = (
            np.frombuffer(buf, dtype=np.uint8, count=byte_count)
            .reshape(h, src.bytesPerLine())[:, : w * 4]
            .reshape(h, w, 4)
            .copy()
        )

        # Cached LUT — when params are unchanged across calls (e.g. the
        # user is painting on a layer below this Curves filter, so the
        # composite re-runs every pen sample but ``points`` is constant),
        # this skips the cubic interp entirely.
        lut = _cached_build_lut(tuple(normalized))

        # Un-premultiply → LUT → Re-premultiply  (BGRA: B=0, G=1, R=2, A=3)
        alpha = arr[:, :, 3].astype(np.float32)
        has_alpha = alpha > 0.0
        adjusted = arr.copy()

        straight = np.zeros_like(arr[:, :, :3], dtype=np.float32)
        for ch in range(3):
            np.divide(
                arr[:, :, ch].astype(np.float32) * 255.0,
                alpha,
                out=straight[:, :, ch],
                where=has_alpha,
            )
        straight = np.clip(straight, 0, 255).astype(np.uint8)

        for ch in range(3):
            straight[:, :, ch] = lut[straight[:, :, ch]]

        for ch in range(3):
            adjusted[:, :, ch] = np.where(
                has_alpha,
                (straight[:, :, ch].astype(np.float32) * alpha / 255.0 + 0.5).astype(np.uint8),
                np.uint8(0),
            )

        if mask is not None:
            adjusted = self._apply_mask(arr, adjusted, mask, w, h)

        result = QImage(adjusted.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied)
        return result.copy()

    @staticmethod
    def _build_lut(points: list[tuple[int, int]]) -> NDArray[Any]:
        """Build a 256-entry LUT from ``(x, y)`` control points.

        Points are sorted and de-duplicated by X before interpolation so
        callers can pass user-supplied lists without pre-processing.
        Always anchors X=0 and X=255 if missing — the LUT must cover the
        full input range.

        The actual computation is cached by :func:`_cached_build_lut` —
        identical inputs across calls reuse the same LUT array.
        """
        normalized = _normalize_points(points)
        return _cached_build_lut(tuple(normalized))

    @classmethod
    def build_lut_from_params(cls, params: dict[str, Any]) -> NDArray[Any]:
        """Resolve points (with legacy migration) and return the 256-entry LUT.

        Used by the GPU processor to upload the curve as a sampler texture.
        """
        return cls._build_lut(_resolve_points(params))

    @staticmethod
    def _apply_mask(
        original: NDArray[Any],
        adjusted: NDArray[Any],
        mask: QImage,
        w: int,
        h: int,
    ) -> NDArray[Any]:
        scaled_mask = mask.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio).convertToFormat(
            QImage.Format.Format_Grayscale8
        )
        bc = scaled_mask.bytesPerLine() * scaled_mask.height()
        b = scaled_mask.bits()
        if hasattr(b, "setsize"):
            b.setsize(bc)
        mask_arr = (
            np.frombuffer(b, dtype=np.uint8, count=bc)
            .reshape(h, scaled_mask.bytesPerLine())[:, :w]
            .copy()
            .astype(np.float32)
            / 255.0
        )
        mask_3d = mask_arr[:, :, np.newaxis]
        blended = (
            original.astype(np.float32) * (1.0 - mask_3d) + adjusted.astype(np.float32) * mask_3d
        )
        return np.clip(blended, 0, 255).astype(np.uint8)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _resolve_points(params: dict[str, Any]) -> list[tuple[int, int]]:
    """Read the curve points from ``params``, migrating legacy formats.

    Order of precedence:
    1. ``params["points"]`` if present and parseable (current format).
    2. Legacy 5-key offset format → converted to a 5-point list using
       the original ``y = x + offset/2`` mapping so old ``.lstroke``
       files keep producing the same curve they were saved with.
    3. Default identity curve.
    """
    raw = params.get("points")
    coerced = _coerce_points_payload(raw)
    if coerced:
        return coerced
    if any(k in params for k in _LEGACY_KEYS):
        return _migrate_legacy_offsets(params)
    return list(_DEFAULT_POINTS)


def _coerce_points_payload(raw: Any) -> list[tuple[int, int]]:
    """Accept tuples / lists / nested sequences; reject anything else."""
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[tuple[int, int]] = []
    for entry in raw:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        try:
            x = int(entry[0])
            y = int(entry[1])
        except (TypeError, ValueError):
            continue
        out.append((max(0, min(255, x)), max(0, min(255, y))))
    return out


def _migrate_legacy_offsets(params: dict[str, Any]) -> list[tuple[int, int]]:
    """Convert the deprecated 5-offset format to a points list.

    Original ``_build_lut`` mapped each offset to ``y = x + offset * 0.5``
    at the matching fixed X position; we apply the same arithmetic here
    so the migrated curve is visually identical to the saved one.
    """
    out: list[tuple[int, int]] = []
    for key, x in zip(_LEGACY_KEYS, _LEGACY_XS, strict=True):
        try:
            offset = int(params.get(key, 0))
        except (TypeError, ValueError):
            offset = 0
        y = max(0, min(255, x + offset // 2 + (offset % 2 if offset > 0 else -(-offset % 2))))
        out.append((x, y))
    return out


def _normalize_points(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort, dedupe by X, and ensure X=0 and X=255 anchors are present.

    Adjacent points sharing an X collapse to the later entry — the user
    can never produce duplicates through the UI, but this protects the
    interpolator (which requires strictly increasing X) from malformed
    saved data.
    """
    if not points:
        return list(_DEFAULT_POINTS)
    cleaned: dict[int, int] = {}
    for x, y in points:
        cx = max(0, min(255, int(x)))
        cy = max(0, min(255, int(y)))
        cleaned[cx] = cy
    if 0 not in cleaned:
        cleaned[0] = cleaned[min(cleaned)]
    if 255 not in cleaned:
        cleaned[255] = cleaned[max(cleaned)]
    return [(x, cleaned[x]) for x in sorted(cleaned)]


def _is_identity(points: list[tuple[int, int]]) -> bool:
    """Detect an identity curve so :meth:`apply` can short-circuit."""
    normalized = _normalize_points(points)
    return all(x == y for x, y in normalized)


def _monotone_cubic_interp(
    xs: NDArray[Any],
    ys: NDArray[Any],
    x_eval: NDArray[Any],
) -> NDArray[Any]:
    """Monotone cubic interpolation (Fritsch-Carlson method).

    Guarantees no overshoot between control points, producing a smooth
    and well-behaved tone curve.
    """
    n = len(xs)
    # Secants
    dx = np.diff(xs)
    dy = np.diff(ys)
    slopes = dy / dx

    # Initial tangents
    m = np.zeros(n)
    m[0] = slopes[0]
    m[-1] = slopes[-1]
    for i in range(1, n - 1):
        if slopes[i - 1] * slopes[i] <= 0:
            m[i] = 0.0
        else:
            m[i] = (slopes[i - 1] + slopes[i]) / 2.0

    # Fritsch-Carlson monotonicity correction
    for i in range(n - 1):
        if abs(slopes[i]) < 1e-10:
            m[i] = 0.0
            m[i + 1] = 0.0
        else:
            alpha = m[i] / slopes[i]
            beta = m[i + 1] / slopes[i]
            tau = alpha * alpha + beta * beta
            if tau > 9.0:
                s = 3.0 / np.sqrt(tau)
                m[i] = s * alpha * slopes[i]
                m[i + 1] = s * beta * slopes[i]

    # Evaluate hermite spline
    result = np.zeros_like(x_eval)
    for i in range(n - 1):
        mask = (x_eval >= xs[i]) & (x_eval <= xs[i + 1])
        if not np.any(mask):
            continue
        t = (x_eval[mask] - xs[i]) / dx[i]
        t2 = t * t
        t3 = t2 * t
        h00 = 2 * t3 - 3 * t2 + 1
        h10 = t3 - 2 * t2 + t
        h01 = -2 * t3 + 3 * t2
        h11 = t3 - t2
        result[mask] = h00 * ys[i] + h10 * dx[i] * m[i] + h01 * ys[i + 1] + h11 * dx[i] * m[i + 1]
    return result


CurvesEffect = CurvesFilter  # Legacy class import compatibility.
BaseEffect = BaseFilter
EffectParamSchema = FilterParamSchema
