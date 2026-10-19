"""Derived, sparse ink views of editable RGB-style vector/text rasters."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy
from loomstroke.core.native_cmyk.color_transform import CmykColorTransform
from loomstroke.core.native_cmyk.pixels import NativeCmykPixels

from .native_cmyk_vector_damage import coverage_alpha, coverage_damage

Region = tuple[int, int, int, int]


class NativeCmykVectorSource:
    """Keep geometry authoritative; inks are disposable projection data only."""

    def __init__(
        self,
        identifier: str,
        profile: bytes,
        dtype: np.dtype[Any],
        logical_size: tuple[int, int] | None = None,
        *,
        bounded: bool = False,
    ) -> None:
        self.profile, self.dtype = profile, dtype
        self.logical_size = logical_size
        self._bounded = bounded
        self._projection_identity = (
            "vector",
            identifier,
            profile,
            dtype.str,
            logical_size,
            bounded,
        )
        self.version = 0
        self.image: QImage
        self._key: tuple[Any, ...] | None = None
        self.color: tuple[int, ...] | None = None
        self.image_origin = (0, 0)
        self._bounds = QRect()
        self._changes: deque[tuple[int, tuple[Region, ...]]] = deque(maxlen=64)
        self.priority_regions: tuple[Region, ...] = ()
        self._coverage: NDArray[Any] | None = None

    def update(
        self,
        image: QImage,
        bounds: QRect,
        origin: tuple[int, int] = (0, 0),
        color: tuple[int, ...] | None = None,
    ) -> None:
        key = (image.cacheKey(), image.size(), origin, color)
        if key == self._key:
            return
        if (
            self._key is not None
            and color is None
            and self.color == color
            and self.image_origin == origin
            and self.image == image
        ):
            # Selection/inline rasterization may allocate a fresh QImage for
            # identical pixels. Do not schedule an endless sequence of jobs.
            self._key, self._bounds = key, QRect(bounds)
            return
        dirty = self._bounds.united(bounds)
        regions: tuple[QRect, ...] = (dirty,)
        priority: tuple[QRect, ...] = ()
        alpha = coverage_alpha(image) if color is not None else None
        if self._key is not None and color is not None and self.color == color:
            damage = coverage_damage(
                self.image,
                self.image_origin,
                image,
                origin,
                before_alpha=self._coverage,
                after_alpha=alpha,
            )
            regions, priority = damage.regions, damage.priority
        self.image = QImage(image)  # Qt implicit sharing; detach before any later layer edit.
        self.color = color
        self.image_origin = origin
        width, height = self.logical_size or (image.width(), image.height())
        self.inks = SimpleNamespace(shape=(height, width, 4), dtype=self.dtype)
        self.alpha = SimpleNamespace(shape=(height, width), dtype=self.dtype)
        self._key, self._bounds = key, QRect(bounds)
        self._coverage = alpha
        if not regions:
            self.priority_regions = ()
            return
        self.version += 1
        self._changes.append(
            (
                self.version,
                tuple((rect.x(), rect.y(), rect.width(), rect.height()) for rect in regions),
            )
        )
        self.priority_regions = tuple(
            (rect.x(), rect.y(), rect.width(), rect.height()) for rect in priority
        )

    def dirty_regions_since(self, revision: int) -> tuple[Region, ...] | None:
        if revision == self.version:
            return ()
        if revision < 0 and self._bounded:
            return ((*self.image_origin, self.image.width(), self.image.height()),)
        if revision < 0 or not self._changes or revision < self._changes[0][0] - 1:
            return None
        return tuple(
            rect for version, regions in self._changes if version > revision for rect in regions
        )

    def region(self, rect: Region) -> NativeCmykPixels:
        if self.color is not None:
            return _convert(self._alpha_region(QRect(*rect)), self.profile, self.dtype, self.color)
        local = QRect(*rect).translated(-self.image_origin[0], -self.image_origin[1])
        rgba = ChannelPixelPolicy.pixels(self.image.copy(local), straight=True)
        return _convert(rgba, self.profile, self.dtype, self.color)

    def _alpha_region(self, rect: QRect) -> NDArray[Any]:
        assert self._coverage is not None
        local = rect.translated(-self.image_origin[0], -self.image_origin[1])
        if self.image.rect().contains(local):
            return self._coverage[
                local.y() : local.y() + local.height(), local.x() : local.x() + local.width()
            ].copy()
        values = np.zeros((rect.height(), rect.width()), np.uint8)
        overlap = local.intersected(self.image.rect())
        if not overlap.isEmpty():
            x, y = overlap.x() - local.x(), overlap.y() - local.y()
            values[y : y + overlap.height(), x : x + overlap.width()] = self._coverage[
                overlap.y() : overlap.y() + overlap.height(),
                overlap.x() : overlap.x() + overlap.width(),
            ]
        return values

    def freeze(
        self, regions: Iterable[Region], offset_x: int, offset_y: int
    ) -> NativeCmykVectorFrame:
        captured: dict[Region, NDArray[Any]] = {}
        height, width = self.inks.shape[:2]
        bounds = QRect(offset_x, offset_y, width, height)
        for region in regions:
            overlap = QRect(*region).intersected(bounds)
            if overlap.isEmpty():
                continue
            local = overlap.translated(-offset_x, -offset_y)
            key = (local.x(), local.y(), local.width(), local.height())
            if self.color is not None:
                values = self._alpha_region(local)
                values.flags.writeable = False
                captured[key] = values
                continue
            # Own raw premultiplied bytes on the UI thread. ICC/unpremultiply
            # happen on the worker; no live QImage crosses the thread boundary.
            bitmap = local.translated(-self.image_origin[0], -self.image_origin[1])
            patch = self.image.copy(bitmap).convertToFormat(
                QImage.Format.Format_RGBA8888_Premultiplied
            )
            rows = np.frombuffer(patch.constBits(), np.uint8).reshape(
                patch.height(), patch.bytesPerLine()
            )
            values = rows[:, : patch.width() * 4].reshape(patch.height(), patch.width(), 4).copy()
            values.flags.writeable = False
            captured[key] = values
        return NativeCmykVectorFrame(self.profile, self.inks, captured, self.color)


class NativeCmykVectorFrame:
    def __init__(
        self,
        profile: bytes,
        inks: SimpleNamespace,
        regions: dict[Region, NDArray[Any]],
        color: tuple[int, ...] | None = None,
    ) -> None:
        self.profile, self.inks, self._regions = profile, inks, regions
        self.color = color
        self._converted: dict[Region, NativeCmykPixels] = {}

    def region(self, rect: Region) -> NativeCmykPixels:
        if rect not in self._converted:
            x, y, width, height = rect
            for (sx, sy, sw, sh), values in self._regions.items():
                if sx <= x and sy <= y and x + width <= sx + sw and y + height <= sy + sh:
                    rgba = values[y - sy : y - sy + height, x - sx : x - sx + width].copy()
                    if self.color is None:
                        # Match the synchronous Qt conversion at antialiased
                        # RGB edges, using only this worker's owned bytes.
                        image = QImage(
                            rgba.data,
                            width,
                            height,
                            rgba.strides[0],
                            QImage.Format.Format_RGBA8888_Premultiplied,
                        )
                        rgba = ChannelPixelPolicy.pixels(image, straight=True)
                    self._converted[rect] = _convert(
                        rgba, self.profile, self.inks.dtype, self.color
                    )
                    break
            else:
                raise ValueError("Vector projection requested pixels outside its owned frame")
        return self._converted[rect]


def _convert(
    rgba: NDArray[Any], profile: bytes, dtype: np.dtype[Any], color: tuple[int, ...] | None = None
) -> NativeCmykPixels:
    if color is not None:
        inks = np.empty((*rgba.shape[:2], 4), dtype)
        inks[:] = color
        scale = 257 if dtype == np.uint16 else 1
        coverage = rgba if rgba.ndim == 2 else rgba[:, :, 3]
        alpha = coverage.astype(dtype) * scale
        return NativeCmykPixels(inks, alpha, profile)
    if not np.any(rgba[:, :, 3]):
        return NativeCmykPixels(
            np.zeros((*rgba.shape[:2], 4), dtype),
            np.zeros(rgba.shape[:2], dtype),
            profile,
        )
    inks, alpha = CmykColorTransform.from_rgb(rgba, profile)
    if dtype == np.uint16:
        inks, alpha = inks.astype(np.uint16) * 257, alpha.astype(np.uint16) * 257
    return NativeCmykPixels(inks, alpha, profile)
