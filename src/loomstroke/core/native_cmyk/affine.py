"""Bounded document-space sampling of native inks without an RGB round trip."""

from __future__ import annotations

import math
from typing import Any, cast

import numpy as np
from PySide6.QtCore import QRect

from loomstroke.core.command_layer_access import _layer_transform
from loomstroke.core.geometry import Affine2D

from .pixels import NativeCmykPixels


def placement(node: Any) -> Affine2D:
    preview = getattr(node, "_preview_transform", None)
    return cast(Affine2D, (preview if preview is not None else _layer_transform(node)).to_affine())


def document_rect(rect: QRect, affine: Affine2D) -> QRect:
    if (
        (affine.a, affine.b, affine.c, affine.d) == (1, 0, 0, 1)
        and affine.tx == round(affine.tx)
        and affine.ty == round(affine.ty)
    ):
        return rect.translated(round(affine.tx), round(affine.ty))
    points = [
        affine.apply(x, y)
        for x in (rect.x() - 0.5, rect.x() + rect.width() + 0.5)
        for y in (rect.y() - 0.5, rect.y() + rect.height() + 0.5)
    ]
    x0, y0 = (math.floor(min(p[i] for p in points)) for i in (0, 1))
    x1, y1 = (math.ceil(max(p[i] for p in points)) for i in (0, 1))
    return QRect(x0, y0, x1 - x0, y1 - y0)


def source_rect(native: Any, rect: QRect, affine: Affine2D) -> QRect:
    """Include the bilinear neighbours, clipped to the actual source footprint."""
    inverse = affine.inverse()
    if inverse is None:
        raise ValueError("CMYK transform must be finite and invertible")
    points = [
        inverse.apply(x, y)
        for x in (rect.x(), rect.x() + rect.width())
        for y in (rect.y(), rect.y() + rect.height())
    ]
    if not all(math.isfinite(value) for point in points for value in point):
        raise ValueError("CMYK transform must be finite and invertible")
    x0, y0 = (math.floor(min(p[i] for p in points)) - 1 for i in (0, 1))
    x1, y1 = (math.ceil(max(p[i] for p in points)) + 1 for i in (0, 1))
    height, width = native.inks.shape[:2]
    return QRect(x0, y0, x1 - x0, y1 - y0).intersected(QRect(0, 0, width, height))


def sample(native: Any, rect: QRect, affine: Affine2D) -> NativeCmykPixels:
    """Resample premultiplied plates and alpha, retaining canonical source values."""
    local = source_rect(native, rect, affine)
    dtype = native.inks.dtype
    shape = (rect.height(), rect.width())
    inks = np.zeros((*shape, 4), dtype)
    alpha = np.zeros(shape, dtype)
    if local.isEmpty():
        return NativeCmykPixels(inks, alpha, native.profile)
    if hasattr(native, "region"):
        source = native.region(local.getRect())
    else:
        x, y, w, h = local.x(), local.y(), local.width(), local.height()
        source = NativeCmykPixels(
            native.inks[y : y + h, x : x + w], native.alpha[y : y + h, x : x + w], native.profile
        )
    if (
        (affine.a, affine.b, affine.c, affine.d) == (1, 0, 0, 1)
        and affine.tx == round(affine.tx)
        and affine.ty == round(affine.ty)
    ):
        target = local.translated(round(affine.tx), round(affine.ty)).intersected(rect)
        if not target.isEmpty():
            sx, sy = (
                target.x() - round(affine.tx) - local.x(),
                target.y() - round(affine.ty) - local.y(),
            )
            dx, dy = target.x() - rect.x(), target.y() - rect.y()
            w, h = target.width(), target.height()
            inks[dy : dy + h, dx : dx + w] = source.inks[sy : sy + h, sx : sx + w]
            alpha[dy : dy + h, dx : dx + w] = source.alpha[sy : sy + h, sx : sx + w]
        return NativeCmykPixels(inks, alpha, native.profile)
    inverse = affine.inverse()
    assert inverse is not None
    yy, xx = np.mgrid[rect.y() : rect.y() + rect.height(), rect.x() : rect.x() + rect.width()]
    sx = inverse.a * (xx + 0.5) + inverse.b * (yy + 0.5) + inverse.tx - 0.5
    sy = inverse.c * (xx + 0.5) + inverse.d * (yy + 0.5) + inverse.ty - 0.5
    ix, iy = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
    fx, fy = sx - ix, sy - iy
    total_alpha = np.zeros(shape, np.float64)
    premultiplied = np.zeros((*shape, 4), np.float64)
    for dx, dy, weight in (
        (0, 0, (1 - fx) * (1 - fy)),
        (1, 0, fx * (1 - fy)),
        (0, 1, (1 - fx) * fy),
        (1, 1, fx * fy),
    ):
        x, y = ix + dx - local.x(), iy + dy - local.y()
        valid = (x >= 0) & (y >= 0) & (x < local.width()) & (y < local.height())
        x, y = x[valid], y[valid]
        covered = source.alpha[y, x].astype(np.float64) * weight[valid]
        total_alpha[valid] += covered
        premultiplied[valid] += source.inks[y, x] * covered[:, None]
    maximum = np.iinfo(dtype).max
    visible = total_alpha > 0
    inks[visible] = np.rint(
        np.clip(premultiplied[visible] / total_alpha[visible, None], 0, maximum)
    ).astype(dtype)
    alpha[:] = np.rint(np.clip(total_alpha, 0, maximum)).astype(dtype)
    return NativeCmykPixels(inks, alpha, native.profile)
