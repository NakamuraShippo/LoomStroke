"""Owned native ink regions for taper/recontour preimages; never round-trip through RGB."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QRect

from loomstroke.core.canvas_command_commit_guard import CanvasCommandCommitGuard
from loomstroke.core.recontour.limits import MAX_REGION_BYTES

from .patch_command import NativeCmykPatchCommand
from .pixels import NativeCmykPixels


class NativeStrokeImage:
    __hash__ = None  # type: ignore[assignment]  # Mutable images are deliberately unhashable.

    def __init__(self, inks: NDArray[Any], alpha: NDArray[Any], profile: bytes) -> None:
        if inks.nbytes + alpha.nbytes > MAX_REGION_BYTES:
            raise ValueError("Native stroke region exceeds 128 MiB")
        self.inks, self.alpha, self.profile = inks, alpha, profile

    @classmethod
    def crop(cls, layer: Any, rect: QRect) -> NativeStrokeImage:
        native = layer.native_cmyk
        local = rect.translated(-layer.offset_x, -layer.offset_y)
        width, height = max(0, rect.width()), max(0, rect.height())
        if width * height * 5 * native.inks.dtype.itemsize > MAX_REGION_BYTES:
            raise ValueError("Native stroke region exceeds 128 MiB")
        result = cls(
            np.zeros((height, width, 4), native.inks.dtype),
            np.zeros((height, width), native.inks.dtype),
            native.profile,
        )
        overlap = local.intersected(QRect(0, 0, native.alpha.shape[1], native.alpha.shape[0]))
        if not overlap.isEmpty():
            x, y, w, h = overlap.x(), overlap.y(), overlap.width(), overlap.height()
            dx, dy = x - local.x(), y - local.y()
            result.inks[dy : dy + h, dx : dx + w] = native.inks[y : y + h, x : x + w]
            result.alpha[dy : dy + h, dx : dx + w] = native.alpha[y : y + h, x : x + w]
        return result

    def width(self) -> int:
        return int(self.alpha.shape[1])

    def height(self) -> int:
        return int(self.alpha.shape[0])

    def isNull(self) -> bool:
        return self.alpha.size == 0

    def copy(self, rect: QRect | None = None) -> NativeStrokeImage:
        if rect is None or rect.isNull():
            return NativeStrokeImage(self.inks.copy(), self.alpha.copy(), self.profile)
        size = max(0, rect.width()) * max(0, rect.height()) * 5 * self.inks.dtype.itemsize
        if size > MAX_REGION_BYTES:
            raise ValueError("Native stroke region exceeds 128 MiB")
        result = NativeStrokeImage(
            np.zeros((max(0, rect.height()), max(0, rect.width()), 4), self.inks.dtype),
            np.zeros((max(0, rect.height()), max(0, rect.width())), self.inks.dtype),
            self.profile,
        )
        result.paste(self, -rect.x(), -rect.y())
        return result

    def paste(self, image: NativeStrokeImage, x: int, y: int) -> None:
        if self.profile != image.profile or self.inks.dtype != image.inks.dtype:
            raise ValueError("Native stroke source profile or depth changed")
        overlap = QRect(x, y, image.width(), image.height()).intersected(
            QRect(0, 0, self.width(), self.height())
        )
        if overlap.isEmpty():
            return
        dx, dy, width, height = overlap.x(), overlap.y(), overlap.width(), overlap.height()
        sx, sy = dx - x, dy - y
        self.inks[dy : dy + height, dx : dx + width] = image.inks[sy : sy + height, sx : sx + width]
        self.alpha[dy : dy + height, dx : dx + width] = image.alpha[
            sy : sy + height, sx : sx + width
        ]

    def snapshot(self) -> NativeCmykPixels:
        return NativeCmykPixels(self.inks, self.alpha, self.profile)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, NativeStrokeImage)
            and self.profile == other.profile
            and self.inks.dtype == other.inks.dtype
            and np.array_equal(self.inks, other.inks)
            and np.array_equal(self.alpha, other.alpha)
        )


def commit_native_image(
    canvas: Any,
    layer: Any,
    region: QRect,
    before: NativeStrokeImage,
    after: NativeStrokeImage,
    label: str,
) -> None:
    local = region.translated(-layer.offset_x, -layer.offset_y)
    rect = (local.x(), local.y(), local.width(), local.height())
    command = NativeCmykPatchCommand(layer.id, rect, before.snapshot(), after.snapshot())
    guard = CanvasCommandCommitGuard(canvas)
    try:
        if not command.apply(canvas):
            raise ValueError("Native stroke replacement could not be committed")
        canvas._record_history_command(label, command)
    except Exception:
        if command._executed:
            guard.rollback(command)
        raise
