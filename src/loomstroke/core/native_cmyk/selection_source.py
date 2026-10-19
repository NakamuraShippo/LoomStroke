"""Sparse native selection overlay in the original layer's source coordinates."""

from collections import deque
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from PySide6.QtCore import QRect

from loomstroke.core.command_image_utils import _affine_bounds
from loomstroke.core.command_layer_access import _layer_transform
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.geometry import Affine2D

from .affine import sample
from .pixels import NativeCmykPixels
from .selection_pixels import compose, over


def rebase(layer: Any, x: int, y: int) -> LayerTransform:
    transform = _layer_transform(layer)
    return replace(
        transform,
        tx=transform.tx + x,
        ty=transform.ty + y,
        origin_x=transform.origin_x - x,
        origin_y=transform.origin_y - y,
    )


def expanded_bounds(native: Any, destination: QRect) -> QRect:
    height, width = native.inks.shape[:2]
    bounds = QRect(0, 0, width, height).united(destination)
    if bounds.width() * bounds.height() * native.inks.dtype.itemsize * 5 > 256 * 1024**2:
        raise ValueError("Native selection exceeds the layer memory limit")
    return bounds


class NativeSelectionSource:
    """Only requested tiles are materialized; the canonical layer is never changed."""

    def __init__(
        self,
        base: Any,
        pixels: NativeCmykPixels,
        transform: Affine2D,
        bounds: QRect,
        selected: tuple[int, ...],
        alpha_lock: bool,
    ) -> None:
        self.base, self.pixels = base, pixels
        self.profile = base.profile
        self.bounds = bounds
        self.transform = compose(Affine2D.translation(-bounds.x(), -bounds.y()), transform)
        shape = bounds.height(), bounds.width()
        self.inks = SimpleNamespace(shape=(*shape, 4), dtype=base.inks.dtype)
        self.alpha = SimpleNamespace(shape=shape, dtype=base.inks.dtype)
        self.selected, self.alpha_lock = selected, alpha_lock
        self.version = 0
        self._projection_identity = object()
        self._dirty: deque[tuple[int, tuple[int, int, int, int]]] = deque(maxlen=64)

    def move(self, transform: Affine2D) -> None:
        transform = compose(Affine2D.translation(-self.bounds.x(), -self.bounds.y()), transform)
        self.version += 1
        height, width = self.pixels.alpha.shape
        for matrix in (self.transform, transform):
            bounds = _affine_bounds(matrix, width, height)
            if bounds is not None:
                rect = (
                    QRect(*bounds)
                    .adjusted(-1, -1, 1, 1)
                    .intersected(QRect(0, 0, self.bounds.width(), self.bounds.height()))
                )
                if not rect.isEmpty():
                    self._dirty.append(
                        (self.version, (rect.x(), rect.y(), rect.width(), rect.height()))
                    )
        self.transform = transform

    def dirty_since(self, version: int) -> tuple[tuple[int, int, int, int], ...] | None:
        if version == self.version:
            return ()
        if not self._dirty or version < self._dirty[0][0] - 1:
            return None
        return tuple(rect for revision, rect in self._dirty if revision > version)

    dirty_regions_since = dirty_since

    def region(self, rect: tuple[int, int, int, int]) -> NativeCmykPixels:
        target = QRect(*rect)
        before = sample(self.base, target, Affine2D.translation(-self.bounds.x(), -self.bounds.y()))
        source = sample(self.pixels, target, self.transform)
        return over(before, source, self.selected, alpha_lock=self.alpha_lock)
