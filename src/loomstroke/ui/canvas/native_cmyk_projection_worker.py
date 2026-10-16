"""Asynchronous GPU jobs with no live Qt images and no stale result publication."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QRect, QTimer
from PySide6.QtGui import QImage

from loomstroke.core.mask_coverage import mask_coverage_alpha8

from .wgpu_backend.native_cmyk_compositor import NativeCmykCompositor


class NativeCmykProjectionWorker:
    def __init__(self, repaint):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cmyk-projection")
        self._future = None
        self._key = None
        self._compositor = None
        self._repaint = repaint
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._poll)
        self._closed = False
        self._mask_cache = {}
        self._coverage_cache = {}

    def patches(self, scene, rect, key):
        if self._future is not None:
            if not self._future.done():
                return None
            future, previous = self._future, self._key
            self._future = None
            result = future.result()  # Propagate GPU failures through the presenter.
            if previous == key:
                return result
        snapshot = self._snapshot(scene)
        bounds = (rect.x(), rect.y(), rect.width(), rect.height())
        self._key = key
        self._future = self._executor.submit(self._render, snapshot, bounds)
        self._timer.start(10)
        return None

    def _poll(self):
        if self._closed or self._future is None:
            return
        if self._future.done():
            self._repaint()
        else:
            self._timer.start(10)

    def _snapshot(self, scene):
        flat = []
        mask_keys = set()

        def node(layer):
            mask = layer.layer_mask
            if mask is not None:
                key = (layer.id, mask.cacheKey())
                mask_keys.add(key)
                owned = self._mask_cache.get(key)
                if owned is None:
                    # Copy raw bytes on the UI thread, without full-image
                    # float normalization or handing live QImages to a worker.
                    rows = np.frombuffer(mask.constBits(), np.uint8).reshape(
                        mask.height(), mask.bytesPerLine()
                    )
                    values = rows.copy()
                    values.flags.writeable = False
                    owned = SimpleNamespace(
                        key=key,
                        values=values,
                        width=mask.width(),
                        format=mask.format(),
                        colors=mask.colorTable(),
                    )
                    self._mask_cache[key] = owned
                mask = owned
            result = SimpleNamespace(
                id=layer.id,
                native_cmyk=layer.native_cmyk,
                layer_type=layer.layer_type,
                visible=layer.visible,
                opacity=layer.opacity,
                blend_mode=layer.blend_mode,
                clipping=layer.clipping,
                layer_mask=mask,
                mask_enabled=layer.mask_enabled,
                offset_x=layer.offset_x,
                offset_y=layer.offset_y,
                transform=layer.transform,
                children=[],
            )
            flat.append(result)
            result.children = [node(child) for child in layer.children]
            return result

        layers = [node(layer) for layer in scene.layers]
        self._mask_cache = {
            key: value for key, value in self._mask_cache.items() if key in mask_keys
        }
        return SimpleNamespace(
            width=scene.width, height=scene.height, layers=layers, flat_layers=flat
        )

    def _render(self, scene, bounds):
        mask_keys = set()
        for layer in scene.flat_layers:
            owned = layer.layer_mask
            if owned is None:
                continue
            mask_keys.add(owned.key)
            coverage = self._coverage_cache.get(owned.key)
            if coverage is None:
                if self._closed:
                    return []
                rows = owned.values
                # This QImage wraps worker-owned immutable bytes, never a
                # live layer image. Preserve palette-backed mask formats.
                image = QImage(rows.data, owned.width, rows.shape[0], rows.shape[1], owned.format)
                if owned.colors:
                    image.setColorTable(owned.colors)
                image = mask_coverage_alpha8(image)
                values = np.frombuffer(image.constBits(), np.uint8).reshape(
                    image.height(), image.bytesPerLine()
                )
                coverage = values[:, : image.width()].copy()
                coverage.flags.writeable = False
                self._coverage_cache[owned.key] = coverage
            layer.layer_mask = coverage
        self._coverage_cache = {
            key: value for key, value in self._coverage_cache.items() if key in mask_keys
        }
        if self._compositor is None:
            self._compositor = NativeCmykCompositor()
        x0, y0, width, height = bounds
        size = self._compositor.TILE
        patches = []
        for y in range(y0, y0 + height, size):
            for x in range(x0, x0 + width, size):
                if self._closed:
                    return []
                rect = QRect(x, y, min(size, x0 + width - x), min(size, y0 + height - y))
                pixels = self._compositor.tile(scene, rect)
                patches.append(((x, y, rect.width(), rect.height()), pixels.preview()))
        return patches

    def _release_gpu(self):
        self._coverage_cache.clear()
        if self._compositor is not None:
            self._compositor.release()
            self._compositor = None

    def release(self):
        self._closed = True
        self._timer.stop()
        self._repaint = lambda: None
        self._mask_cache.clear()
        self._executor.submit(self._release_gpu)
        self._executor.shutdown(wait=False)
