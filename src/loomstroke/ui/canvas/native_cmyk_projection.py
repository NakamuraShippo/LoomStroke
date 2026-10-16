"""Cache the ICC display of GPU ink composites and update changed regions only."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter

from loomstroke.core.canvas_models import VectorStateSnapshot
from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy

from .native_cmyk_projection_worker import NativeCmykProjectionWorker
from .render_scene import RenderLayerSnapshot
from .wgpu_backend.device_manager import WgpuDeviceManager
from .wgpu_backend.native_cmyk_compositor import NativeCmykCompositor


class NativeCmykProjection:
    @classmethod
    def prepare(cls, presenter, scene):
        projection = getattr(presenter, "_native_cmyk_projection", None)
        if projection is None and any(layer.native_cmyk is not None for layer in scene.flat_layers):
            manager = presenter.device_manager
            if manager is None and getattr(presenter, "_runtime", None) is not None:
                manager = WgpuDeviceManager(runtime=presenter._runtime)
            projection = cls(manager, repaint=getattr(presenter, "_native_cmyk_repaint", None))
            presenter._native_cmyk_projection = projection
        return projection.scene(scene) if projection is not None else scene

    def __init__(self, device_manager=None, *, repaint=None):
        self._manager = device_manager
        self._compositor = None
        self._image = None
        self._structure = None
        self._versions = {}
        self._version = 0
        self.last_tile_count = 0
        self.pending = False
        self._worker = NativeCmykProjectionWorker(repaint) if repaint is not None else None

    def scene(self, scene):
        native_layers = [layer for layer in scene.flat_layers if layer.native_cmyk is not None]
        if not native_layers or scene.channel_view is not None:
            return scene
        structure = (
            scene.width,
            scene.height,
            tuple(
                (
                    layer.id,
                    id(layer.native_cmyk),
                    layer.parent_id,
                    layer.layer_type,
                    layer.visible,
                    layer.opacity,
                    str(layer.blend_mode),
                    layer.clipping,
                    layer.offset_x,
                    layer.offset_y,
                    layer.mask_enabled,
                    layer.layer_mask.cacheKey() if layer.layer_mask is not None else 0,
                    repr(layer.transform),
                )
                for layer in scene.flat_layers
            ),
        )
        bounds = QRect(0, 0, scene.width, scene.height)
        dirty = QRect()
        if self._structure != structure:
            dirty = bounds
            if self._image is None or self._image.size() != bounds.size():
                self._image = QImage(
                    scene.width, scene.height, QImage.Format.Format_ARGB32_Premultiplied
                )
                self._image.fill(Qt.GlobalColor.transparent)
        else:
            dirty = self._dirty_regions(native_layers, bounds)
        self.last_tile_count = 0
        dirty = dirty.intersected(bounds)
        versions = {layer.id: getattr(layer.native_cmyk, "version", 0) for layer in native_layers}
        self.pending = False
        if not dirty.isEmpty():
            # Build every changed tile before publishing a new preview.
            patches = []
            if self._worker is not None and (
                self._compositor is None or dirty.width() * dirty.height() > 256 * 256
            ):
                result = self._worker.patches(scene, dirty, (structure, tuple(versions.items())))
                if result is None:
                    self.pending = True
                else:
                    patches = [
                        (QRect(*rect), ChannelPixelPolicy.image(rgba, straight=True))
                        for rect, rgba in result
                    ]
            else:
                patches = self._sync_patches(scene, dirty)
            if patches:
                self._publish(patches)
        if not self.pending:
            self._structure = structure
            self._versions = versions
        else:
            dirty = QRect()
        return self._scene(scene, dirty)

    def _sync_patches(self, scene, dirty):
        if self._compositor is None:
            self._compositor = NativeCmykCompositor(self._manager)
        size = self._compositor.TILE
        patches = []
        for y in range(dirty.y(), dirty.y() + dirty.height(), size):
            for x in range(dirty.x(), dirty.x() + dirty.width(), size):
                rect = QRect(
                    x, y, min(size, dirty.right() - x + 1), min(size, dirty.bottom() - y + 1)
                )
                pixels = self._compositor.tile(scene, rect)
                patches.append((rect, ChannelPixelPolicy.image(pixels.preview(), straight=True)))
        return patches

    def _dirty_regions(self, layers, bounds):
        dirty = QRect()
        for layer in layers:
            native = layer.native_cmyk
            revision = self._versions.get(layer.id, -1)
            changes = (
                native.dirty_since(revision)
                if hasattr(native, "dirty_since")
                else (() if revision == 0 else None)
            )
            if changes is None:
                return bounds
            for x, y, width, height in changes:
                dirty = dirty.united(QRect(x + layer.offset_x, y + layer.offset_y, width, height))
        return dirty

    def _publish(self, patches):
        image = self._image.copy()
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        for rect, patch in patches:
            painter.drawImage(rect.topLeft(), patch)
        painter.end()
        self._image = image
        self._version += 1
        self.last_tile_count = len(patches)

    def _scene(self, scene, dirty):
        layer = RenderLayerSnapshot(
            "native-cmyk-projection",
            "CMYK",
            True,
            1.0,
            "normal",
            self._version,
            image=self._image,
            preview_dirty_rect=(
                (dirty.x(), dirty.y(), dirty.width(), dirty.height())
                if not dirty.isEmpty()
                else None
            ),
        )
        return replace(
            scene,
            layers=(layer,),
            flat_layers=(layer,),
            parent_ids=(None,),
            active_flat_index=0,
            vector=VectorStateSnapshot(),
        )

    def release(self):
        if self._worker is not None:
            self._worker.release()
            self._worker = None
        if self._compositor is not None:
            self._compositor.release()
        self._compositor = None
        self._image = None
        self._structure = None
        self._versions.clear()
