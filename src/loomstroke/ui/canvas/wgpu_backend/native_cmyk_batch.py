"""Submit bounded groups of ink tiles and map one reusable staging buffer."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
from PySide6.QtCore import QRect

from loomstroke.core.appearance.live_styles import layer_styles, style_padding
from loomstroke.core.native_cmyk.affine import placement
from loomstroke.core.native_cmyk.pixels import NativeCmykPixels

from .native_cmyk_buffer_pool import NativeCmykBufferPool


def _style_tile_size(scene: Any) -> int:
    size = 256
    for node in scene.flat_layers:
        styles = layer_styles(node)
        if not styles.enabled():
            continue
        padding = style_padding(styles) + 2
        size = min(size, 128 if padding > 96 else 256)
        if node.native_cmyk is None or max(node.native_cmyk.inks.shape[:2]) + 2 * padding <= 768:
            continue
        inverse = placement(node).inverse()
        if inverse is not None:
            stretch = max(abs(inverse.a) + abs(inverse.b), abs(inverse.c) + abs(inverse.d), 1.0)
            size = min(size, max(1, int(max(16, 768 - 2 * padding) / stretch)))
    return size


class NativeCmykBatch:
    def __init__(self, bundle: Any) -> None:
        self.bundle = bundle
        self.pool = NativeCmykBufferPool(bundle)

    def upload(self, data: Any, usage: int) -> Any:
        values = np.ascontiguousarray(data)
        buffer = self.pool.acquire(values.nbytes, usage | self.bundle.wgpu.BufferUsage.COPY_DST)
        self.bundle.queue.write_buffer(buffer, 0, values)
        return buffer

    def render(
        self, compositor: Any, scene: Any, regions: Iterable[QRect]
    ) -> list[NativeCmykPixels]:
        compositor._canvas_bounds = QRect(0, 0, scene.width, scene.height)
        rects = [rect.intersected(compositor._canvas_bounds) for rect in regions]
        if any(rect.isEmpty() or rect.width() > 256 or rect.height() > 256 for rect in rects):
            raise ValueError("Native composite requires a bounded canvas tile")
        if not rects:
            return []
        native = next(
            (layer.native_cmyk for layer in scene.flat_layers if layer.native_cmyk is not None),
            None,
        )
        if native is None:
            raise ValueError("No native CMYK layers")
        compositor._native_profile = native.profile
        if any(layer_styles(layer).enabled() for layer in scene.flat_layers):
            # Styles need a source halo. Submit each bounded work item before
            # reusing its buffers, including at the maximum shadow offset.
            size = _style_tile_size(scene)
            result = []
            for rect in rects:
                inks = np.empty((rect.height(), rect.width(), 4), native.inks.dtype)
                alpha = np.empty((rect.height(), rect.width()), native.inks.dtype)
                for y in range(rect.y(), rect.y() + rect.height(), size):
                    for x in range(rect.x(), rect.x() + rect.width(), size):
                        part = QRect(
                            x, y, min(size, rect.right() - x + 1), min(size, rect.bottom() - y + 1)
                        )
                        pixels = self._render_group(compositor, scene, [part], native)[0]
                        dx, dy = x - rect.x(), y - rect.y()
                        inks[dy : dy + part.height(), dx : dx + part.width()] = pixels.inks
                        alpha[dy : dy + part.height(), dx : dx + part.width()] = pixels.alpha
                result.append(NativeCmykPixels(inks, alpha, native.profile))
            return result
        # Conservative allowance for source/output/mask/group buffers and readback.
        largest = max(rect.width() * rect.height() for rect in rects)
        per_tile = largest * (40 + 64 * len(scene.flat_layers)) + 32 * len(scene.flat_layers)
        limit = max(1, min(4, self.pool.LIMIT // max(1, per_tile)))
        result = []
        for start in range(0, len(rects), limit):
            result.extend(
                self._render_group(compositor, scene, rects[start : start + limit], native)
            )
        return result

    def _render_group(
        self, compositor: Any, scene: Any, rects: list[QRect], native: Any
    ) -> list[NativeCmykPixels]:
        compositor._buffers = []
        compositor._allocated_bytes = 0
        compositor._encoder = self.bundle.device.create_command_encoder()
        lengths = [rect.width() * rect.height() * 5 * 4 for rect in rects]
        total = sum(lengths)
        try:
            flags = self.bundle.wgpu.BufferUsage
            staging = self.pool.acquire(total, flags.MAP_READ | flags.COPY_DST)
            offset = 0
            for rect, length in zip(rects, lengths, strict=True):
                compositor._rect = rect
                compositor._count = rect.width() * rect.height()
                zero = compositor._buffer(np.zeros((compositor._count, 5), np.float32))
                compositor._captured = None
                result = compositor._stack(scene.layers, zero, 0)
                if compositor.capture_id is not None:
                    if compositor._captured is None:
                        # Empty/off-canvas sources legitimately contribute no
                        # foreground to this tile. The lower stack stays out.
                        compositor._encoder.copy_buffer_to_buffer(zero, 0, staging, offset, length)
                        offset += length
                        continue
                    captured, bounds = compositor._captured
                    for row in range(rect.height()):
                        source_offset = (
                            (rect.y() + row - bounds.y()) * bounds.width() + rect.x() - bounds.x()
                        ) * 20
                        compositor._encoder.copy_buffer_to_buffer(
                            captured,
                            source_offset,
                            staging,
                            offset + row * rect.width() * 20,
                            rect.width() * 20,
                        )
                else:
                    compositor._encoder.copy_buffer_to_buffer(result, 0, staging, offset, length)
                offset += length
            self.bundle.queue.submit([compositor._encoder.finish()])
            staging.map_sync(self.bundle.wgpu.MapMode.READ, 0, total)
            try:
                data = np.frombuffer(staging.read_mapped(0, total), np.float32)
            finally:
                staging.unmap()
            maximum = np.iinfo(native.inks.dtype).max
            result = []
            offset = 0
            for rect, length in zip(rects, lengths, strict=True):
                count = length // 4
                values = np.rint(np.clip(data[offset : offset + count], 0, 1) * maximum).astype(
                    native.inks.dtype
                )
                values = values.reshape(rect.height(), rect.width(), 5)
                result.append(NativeCmykPixels(values[:, :, :4], values[:, :, 4], native.profile))
                offset += count
            return result
        except BaseException:
            self.pool.release()
            raise
        finally:
            self.pool.finish()
            compositor._buffers = []
            compositor._encoder = None

    def release(self) -> None:
        self.pool.release()
