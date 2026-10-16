"""Evaluate filter brush dabs in document space over bounded source tiles."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRect


def iter_placed_dab(coverage, point, document_rect, diameter, flow, tone, tilt_strength):
    geometry = coverage.geometry
    local = geometry.source_rect(document_rect)
    if local.isEmpty():
        return
    tip_cache = {}
    for ty in range(local.top() // 128, local.bottom() // 128 + 1):
        for tx in range(local.left() // 128, local.right() // 128 + 1):
            rect = coverage._tile_rect((tx, ty)).intersected(local)
            x, y = geometry.document_grid(rect)
            if coverage.tip_path:
                size = max(1, round(diameter))
                tip = coverage._tip_raster(size, tip_cache)
                ix = np.floor(x - round(point.x - size / 2)).astype(np.int64)
                iy = np.floor(y - round(point.y - size / 2)).astype(np.int64)
                valid = (ix >= 0) & (iy >= 0) & (ix < size) & (iy < size)
                mask = np.zeros(x.shape, dtype=np.float32)
                rgba = tip.pixels()[iy[valid], ix[valid]]
                values = rgba[:, 3].astype(np.float32) / 255
                if tip.opaque:
                    values = np.sum(rgba[:, :3] * np.array([0.299, 0.587, 0.114]), axis=1) / 255
                mask[valid] = values
                if coverage.hardness_mode == "radial" and coverage.brush.hardness < 1:
                    distance = np.hypot(ix - (size - 1) / 2, iy - (size - 1) / 2) / max(1, size / 2)
                    edge = np.clip(
                        (distance - coverage.brush.hardness)
                        / max(1e-6, 1 - coverage.brush.hardness),
                        0,
                        1,
                    )
                    mask *= 1 - edge * edge * (3 - 2 * edge)
            else:
                radius = diameter / 2
                distance = np.hypot(x - point.x, y - point.y)
                inner = radius * max(0, min(1, coverage.brush.hardness))
                mask = np.clip((radius + 0.5 - distance) / max(0.5, radius - inner), 0, 1)
            amount = np.clip(mask * flow * tone * tilt_strength, 0, 1)
            view = coverage.array[
                rect.y() : rect.y() + rect.height(), rect.x() : rect.x() + rect.width()
            ]
            updated = np.rint(view + (255 - view) * amount).astype(np.uint8)
            if np.array_equal(view, updated):
                yield QRect()
                continue
            view[:] = updated
            coverage._tiles.add((tx, ty))
            coverage._dirty_tiles.add((tx, ty))
            yield geometry.document_rect(rect)
