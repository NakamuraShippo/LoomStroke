"""Asynchronous GPU jobs with no live Qt images and no stale result publication."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.appearance.live_styles import document_style_padding, layer_styles
from loomstroke.core.mask_coverage import mask_coverage_alpha8
from loomstroke.core.native_cmyk.affine import placement, source_rect

from .native_cmyk_completion import NativeCmykCompletion
from .native_cmyk_display_frame import NativeCmykDisplayCache
from .native_cmyk_frame_snapshot import NativeCmykFrameSnapshot, split_regions
from .wgpu_backend.native_cmyk_compositor import NativeCmykCompositor
from .wgpu_backend.native_layer_style_processor import NativeLayerStyleProcessor


def _freeze_pixels(native: Any, regions: Any, x: int, y: int, affine: Any = None) -> Any:
    if affine is not None and (affine.a, affine.b, affine.c, affine.d, affine.tx, affine.ty) != (
        1,
        0,
        0,
        1,
        x,
        y,
    ):
        regions = tuple(
            source_rect(native, QRect(*region), affine).translated(x, y).getRect()
            for region in regions
        )
    freeze = getattr(native, "freeze", None)
    return (
        freeze(regions, x, y)
        if freeze is not None
        else NativeCmykFrameSnapshot(native, regions, x, y)
    )


class NativeCmykProjectionWorker:
    def __init__(self, repaint):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cmyk-projection")
        self._future = None
        self._key = None
        self._compositor = None
        self._completion = NativeCmykCompletion(repaint)
        self._display_cache = NativeCmykDisplayCache()
        self._closed = False
        self._mask_cache = {}
        self._coverage_cache = {}
        self.preparing = False
        self.materialize_image = False

    def patches(self, scene, rect, key, *, allow_older=False, inherit_contexts=()):
        if self._future is not None:
            if not self._future.done():
                return None
            future, previous = self._future, self._key
            self._future = None
            result = future.result()  # Propagate GPU failures through the presenter.
            same_context = (
                allow_older
                and isinstance(previous, tuple)
                and isinstance(key, tuple)
                and previous[0] == key[0]
            )
            if previous == key or same_context:
                return result
        regions = split_regions((rect,)) if isinstance(rect, QRect) else split_regions(rect)
        snapshot = self._snapshot(scene, regions)
        snapshot.projection_key = key
        snapshot.inherit_contexts = inherit_contexts
        self._key = key
        self._future = self._executor.submit(self._render, snapshot, regions)
        self._future.add_done_callback(self._completion.notify)
        return None

    def _snapshot(self, scene, regions=None):
        flat = []
        mask_keys = set()
        if regions is None:
            regions = split_regions(((0, 0, scene.width, scene.height),))

        def node(layer, parent_visible=True, halo=0):
            visible = parent_visible and layer.visible
            styles = layer_styles(layer)
            halo += document_style_padding(layer)
            captured_regions = tuple(
                QRect(*r).adjusted(-halo, -halo, halo, halo).getRect() for r in regions
            )
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
                native_cmyk=(
                    _freeze_pixels(
                        layer.native_cmyk,
                        captured_regions if visible else (),
                        layer.offset_x,
                        layer.offset_y,
                        placement(layer),
                    )
                    if layer.native_cmyk is not None
                    else None
                ),
                layer_type=layer.layer_type,
                vector_kind=getattr(layer, "vector_kind", "draw"),
                visible=layer.visible,
                opacity=layer.opacity,
                blend_mode=layer.blend_mode,
                clipping=layer.clipping,
                layer_mask=mask,
                mask_enabled=layer.mask_enabled,
                offset_x=layer.offset_x,
                offset_y=layer.offset_y,
                transform=getattr(layer, "_preview_transform", None) or layer.transform,
                _preview_transform=getattr(layer, "_preview_transform", None),
                children=[],
                style_stack=styles,
            )
            flat.append(result)
            result.children = [node(child, visible, halo) for child in layer.children]
            return result

        layers = [node(layer) for layer in scene.layers]
        self._mask_cache = {
            key: value for key, value in self._mask_cache.items() if key in mask_keys
        }
        return SimpleNamespace(
            width=scene.width, height=scene.height, layers=layers, flat_layers=flat
        )

    def _render(self, scene, bounds):
        if self._closed:
            return []
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
        self._prepare_gpu(scene)
        if self._closed:
            return []
        patches = []
        render_tiles = getattr(self._compositor, "tiles", None)
        rects = [QRect(*region) for region in bounds]
        tiles = (
            render_tiles(scene, rects)
            if callable(render_tiles)
            else [self._compositor.tile(scene, rect) for rect in rects]
        )
        for region, pixels in zip(bounds, tiles, strict=True):
            if self._closed:
                return []
            patches.append((region, pixels.preview()))
        frame = self._display_cache.publish(
            scene.width,
            scene.height,
            patches,
            scene.projection_key,
            inherit_contexts=getattr(scene, "inherit_contexts", ()),
        )
        if self.materialize_image:
            _ = frame.image
        return frame

    def _prepare_gpu(self, scene):
        self.preparing = True
        try:
            if self._compositor is None:
                self._compositor = NativeCmykCompositor()
            if getattr(self._compositor, "_style_processor", False) is None and any(
                layer_styles(layer).enabled() for layer in scene.flat_layers
            ):
                self._compositor._style_processor = NativeLayerStyleProcessor(
                    self._compositor.bundle
                )
        finally:
            self.preparing = False

    def _release_gpu(self):
        self._coverage_cache.clear()
        if self._compositor is not None:
            self._compositor.release()
            self._compositor = None

    def release(self):
        self._closed = True
        self._completion.close()
        self._mask_cache.clear()
        self._executor.submit(self._release_gpu)
        self._executor.shutdown(wait=False)
