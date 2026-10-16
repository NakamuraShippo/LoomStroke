"""Bounded local brush dabs accumulating a filter coverage mask."""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage

from loomstroke.core.command_types import BrushSettings, StrokePoint
from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names

TILE_SIZE = 128


@dataclass(slots=True)
class _TipRaster:
    image: QImage
    opaque: bool

    def pixels(self) -> np.ndarray:
        rows = np.frombuffer(self.image.constBits(), dtype=np.uint8).reshape(
            self.image.height(), self.image.bytesPerLine()
        )
        return rows[:, : self.image.width() * 4].reshape(self.image.height(), self.image.width(), 4)


class FilterPenCoverage:
    def __init__(
        self,
        size: tuple[int, int],
        origin: tuple[int, int],
        brush: BrushSettings,
        tip_path: str | None = None,
        hardness_mode: str = "tip",
        tilt_angle: bool = False,
        geometry=None,
    ) -> None:
        self.origin = origin
        self.brush = brush
        self.tip_path = tip_path
        self.hardness_mode = hardness_mode
        self.tilt_angle = tilt_angle
        self.geometry = geometry
        self.array = np.zeros((size[1], size[0]), dtype=np.uint8)
        self.bounds = QRect(origin[0], origin[1], size[0], size[1])
        self._tiles: set[tuple[int, int]] = set()
        self._dirty_tiles: set[tuple[int, int]] = set()
        self._tips: dict[int, _TipRaster] = {}
        self._tip_image = QImage(tip_path) if tip_path else None
        if self._tip_image is not None and self._tip_image.isNull():
            raise ValueError("The brush tip image could not be read.")

    def coverage(self, region: QRect) -> np.ndarray:
        """Return a detached mask for a source-local rectangle."""
        result = np.zeros((max(0, region.height()), max(0, region.width())), dtype=np.uint8)
        overlap = region.intersected(QRect(0, 0, self.bounds.width(), self.bounds.height()))
        if overlap.isEmpty():
            return result
        x, y = overlap.x() - region.x(), overlap.y() - region.y()
        result[y : y + overlap.height(), x : x + overlap.width()] = self.array[
            overlap.y() : overlap.y() + overlap.height(),
            overlap.x() : overlap.x() + overlap.width(),
        ]
        return result

    def dirty_tiles(self, *, pending: bool = False) -> frozenset[tuple[int, int]]:
        """Return source-local tile indices, retaining current tiles by default."""
        return frozenset(self._dirty_tiles if pending else self._tiles)

    def take_dirty_tiles(self) -> set[tuple[int, int]]:
        """Consume tiles changed since the preceding call."""
        result, self._dirty_tiles = self._dirty_tiles, set()
        return result

    def clear_path(self) -> QRect:
        """Clear coverage and invalidate old tiles without scanning source pixels."""
        dirty = QRect()
        for key in self._tiles:
            local = self._tile_rect(key)
            rect = (
                self.geometry.document_rect(local)
                if self.geometry
                else local.translated(*self.origin)
            )
            dirty = rect if dirty.isEmpty() else dirty.united(rect)
        self._dirty_tiles.update(self._tiles)
        self._tiles.clear()
        self.array.fill(0)
        return dirty

    def reset_path(self, points: list[StrokePoint]) -> QRect:
        self._validate(points)
        old = self.clear_path()
        new = self.add(points)
        return new if old.isEmpty() else old if new.isEmpty() else old.united(new)

    def _tile_rect(self, key: tuple[int, int]) -> QRect:
        return QRect(key[0] * TILE_SIZE, key[1] * TILE_SIZE, TILE_SIZE, TILE_SIZE).intersected(
            QRect(0, 0, self.bounds.width(), self.bounds.height())
        )

    @staticmethod
    def _validate(points: list[StrokePoint]) -> None:
        if not all(
            math.isfinite(value)
            for point in points
            for value in (point.x, point.y, point.pressure, point.tilt_x, point.tilt_y)
        ):
            raise ValueError("Filter Pen coverage requires finite stroke samples.")

    def add(self, points: list[StrokePoint]) -> QRect:
        self._validate(points)
        dirty = QRect()
        for point in points:
            region = self._dab(point)
            if not region.isEmpty():
                dirty = region if dirty.isEmpty() else dirty.united(region)
        return dirty

    def _dab(self, point: StrokePoint) -> QRect:
        dirty = QRect()
        for region in self.iter_add(point):
            if not region.isEmpty():
                dirty = region if dirty.isEmpty() else dirty.united(region)
        return dirty

    def iter_add(self, point: StrokePoint) -> Iterator[QRect]:
        """Apply a dab one source-local tile at a time, yielding document dirties.

        An unchanged tile yields an empty rectangle so the caller can enforce
        its processing budget even when an already saturated dab is repeated.
        """
        self._validate([point])
        pressure = max(0.0, min(1.0, point.pressure))
        flow = max(0.0, min(1.0, self.brush.flow))
        if pressure <= 0 or flow <= 0:
            return
        width_scale, tilt_strength = 1.0, 1.0
        if self.tilt_angle and (point.tilt_x or point.tilt_y):
            magnitude = min(1.0, math.hypot(point.tilt_x, point.tilt_y) / 60.0)
            width_scale, tilt_strength = 0.5 + 0.5 * magnitude, 0.7 + 0.3 * magnitude
        diameter = max(
            1.0, self.brush.size * pressure * width_scale * (2.0 if self.tip_path else 1.0)
        )
        radius = diameter / 2.0
        full = QRect(
            math.floor(point.x - radius - 1),
            math.floor(point.y - radius - 1),
            math.ceil(diameter + 3),
            math.ceil(diameter + 3),
        )
        if self.geometry is not None:
            from .placed_coverage import iter_placed_dab  # noqa: PLC0415

            tone = (1.0 - self.brush.tone_range) + self.brush.tone_range * (
                pressure**self.brush.tone_gamma
            )
            yield from iter_placed_dab(self, point, full, diameter, flow, tone, tilt_strength)
            return
        region = full.intersected(self.bounds)
        if region.isEmpty():
            return
        tone = (1.0 - self.brush.tone_range) + self.brush.tone_range * (
            pressure**self.brush.tone_gamma
        )
        local = region.translated(-self.origin[0], -self.origin[1])
        dab_tip: dict[int, _TipRaster] = {}
        for y in range(local.top() // TILE_SIZE, local.bottom() // TILE_SIZE + 1):
            for x in range(local.left() // TILE_SIZE, local.right() // TILE_SIZE + 1):
                patch = self._tile_rect((x, y)).translated(*self.origin).intersected(region)
                yield self._dab_region(point, patch, diameter, flow, tone, tilt_strength, dab_tip)

    def _dab_region(
        self,
        point: StrokePoint,
        region: QRect,
        diameter: float,
        flow: float,
        tone: float,
        tilt_strength: float,
        dab_tip: dict[int, _TipRaster] | None = None,
    ) -> QRect:
        radius = diameter / 2.0
        if self.tip_path:
            mask = self._tip_patch(point, diameter, region, dab_tip)
        else:
            x = np.arange(region.x(), region.x() + region.width(), dtype=np.float32) + 0.5
            y = np.arange(region.y(), region.y() + region.height(), dtype=np.float32) + 0.5
            distance = np.hypot(x[None, :] - point.x, y[:, None] - point.y)
            hardness = max(0.0, min(1.0, self.brush.hardness))
            inner = radius * hardness
            mask = np.clip((radius + 0.5 - distance) / max(0.5, radius - inner), 0, 1)
        amount = np.clip(mask * flow * tone * tilt_strength, 0, 1)
        ox, oy = self.origin
        view = self.array[
            region.y() - oy : region.y() - oy + region.height(),
            region.x() - ox : region.x() - ox + region.width(),
        ]
        updated = np.rint(view + (255.0 - view) * amount).astype(np.uint8)
        changed = updated != view
        if not changed.any():
            return QRect()
        view[:] = updated
        key = ((region.x() - ox) // TILE_SIZE, (region.y() - oy) // TILE_SIZE)
        self._tiles.add(key)
        self._dirty_tiles.add(key)
        return region

    def _tip_raster(self, size: int, dab_tip: dict[int, _TipRaster] | None) -> _TipRaster:
        tip = dab_tip.get(size) if dab_tip is not None else None
        if tip is None:
            tip = self._tips.get(size)
        if tip is None:
            assert self._tip_image is not None
            image = self._tip_image.scaled(
                size,
                size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ).convertToFormat(QImage.Format.Format_RGBA8888)
            rows = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(
                size, image.bytesPerLine()
            )
            rgba = rows[:, : size * 4].reshape(size, size, 4)
            tip = _TipRaster(image, bool(rgba[:, :, 3].min() == 255))
            # Cache by pressure-scaled size, bounded to avoid retaining many huge tips.
            cache_bytes = sum(cached.image.sizeInBytes() for cached in self._tips.values())
            if cache_bytes + image.sizeInBytes() <= 4 * 1024 * 1024 and len(self._tips) < 32:
                self._tips[size] = tip
        # Oversized tips live only as long as this dab's iterator, not per tile.
        if dab_tip is not None:
            dab_tip[size] = tip
        return tip

    def _tip_patch(
        self,
        point: StrokePoint,
        diameter: float,
        region: QRect,
        dab_tip: dict[int, _TipRaster] | None = None,
    ) -> np.ndarray:
        size = max(1, round(diameter))
        tip = self._tip_raster(size, dab_tip)
        result = np.zeros((region.height(), region.width()), dtype=np.float32)
        tip_rect = QRect(round(point.x - size / 2), round(point.y - size / 2), size, size)
        overlap = tip_rect.intersected(region)
        if not overlap.isEmpty():
            x, y = overlap.x() - region.x(), overlap.y() - region.y()
            tx, ty = overlap.x() - tip_rect.x(), overlap.y() - tip_rect.y()
            rgba = tip.pixels()[ty : ty + overlap.height(), tx : tx + overlap.width()]
            mask = rgba[:, :, 3].astype(np.float32) / 255.0
            if tip.opaque:
                mask = np.sum(rgba[:, :, :3] * np.array([0.299, 0.587, 0.114]), axis=2) / 255
            if self.hardness_mode == "radial" and self.brush.hardness < 1:
                xx = np.arange(tx, tx + overlap.width())
                yy = np.arange(ty, ty + overlap.height())
                radius = max(1.0, size / 2)
                distance = (
                    np.hypot(xx[None, :] - (size - 1) / 2, yy[:, None] - (size - 1) / 2) / radius
                )
                transition = np.clip(
                    (distance - self.brush.hardness) / max(1e-6, 1 - self.brush.hardness), 0, 1
                )
                mask *= 1 - transition * transition * (3 - 2 * transition)
            result[y : y + overlap.height(), x : x + overlap.width()] = mask
        return result


# Compatibility aliases for existing imports.
EffectPenCoverage = FilterPenCoverage


_bind_legacy_names(__name__, {"EffectPenCoverage": "FilterPenCoverage"})
