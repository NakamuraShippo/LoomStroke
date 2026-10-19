"""Cache the ICC display of GPU ink composites and update changed regions only."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter

from loomstroke.core.appearance.live_styles import style_halos, style_signature
from loomstroke.core.canvas_invalidation import RenderInvalidation
from loomstroke.core.canvas_models import VectorStateSnapshot
from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy
from loomstroke.core.native_cmyk.affine import document_rect, placement

from .native_cmyk_dirty_queue import NativeCmykDirtyQueue
from .native_cmyk_display_frame import NativeCmykDisplayFrame
from .native_cmyk_display_tiles import NativeCmykDisplayTiles
from .native_cmyk_frame_snapshot import split_regions
from .native_cmyk_projection_worker import NativeCmykProjectionWorker
from .native_cmyk_vector_damage import NativeCmykPaintDamage
from .native_cmyk_vector_scene import NativeCmykVectorScene
from .render_scene import RenderLayerSnapshot
from .wgpu_backend.device_manager import WgpuDeviceManager
from .wgpu_backend.native_cmyk_compositor import NativeCmykCompositor


class NativeCmykProjection:
    @staticmethod
    def display_invalidation(scene, previous, signature, invalidation):
        if (
            invalidation is not None
            and len(scene.layers) == 1
            and scene.layers[0].id == "native-cmyk-projection"
            and previous == signature
        ):
            return RenderInvalidation(gpu_resources=invalidation.gpu_resources)
        return invalidation

    @classmethod
    def prepare(cls, presenter, scene):
        projection = getattr(presenter, "_native_cmyk_projection", None)
        if projection is None and any(layer.native_cmyk is not None for layer in scene.flat_layers):
            manager = getattr(presenter, "device_manager", None)
            if manager is None:
                manager = getattr(getattr(presenter, "_renderer", None), "device_manager", None)
            if manager is None and getattr(presenter, "_runtime", None) is not None:
                manager = WgpuDeviceManager(runtime=presenter._runtime)
            projection = cls(manager, repaint=getattr(presenter, "_native_cmyk_repaint", None))
            presenter._native_cmyk_projection = projection
        return (
            projection.scene(
                scene,
                tiled_display=bool(getattr(presenter, "_supports_native_display_tiles", False)),
            )
            if projection is not None
            else scene
        )

    def __init__(self, device_manager=None, *, repaint=None):
        self._manager = device_manager
        self._compositor = None
        self._frame: NativeCmykDisplayFrame | None = None
        self._legacy_image: QImage | None = None
        self._display_size = None
        self._structure = None
        self._requested_structure = None
        self._versions = {}
        self._observed_versions = {}
        self._dirty_queue = NativeCmykDirtyQueue()
        self._version = 0
        self.last_tile_count = 0
        self.pending = False
        self._worker = NativeCmykProjectionWorker(repaint) if repaint is not None else None
        self._vectors = NativeCmykVectorScene()
        self._paint_damage = NativeCmykPaintDamage()
        self._inherit_contexts: tuple[Any, ...] = ()

    @property
    def _image(self) -> QImage | None:
        return self._frame.image if self._frame is not None else self._legacy_image

    @_image.setter
    def _image(self, value: QImage | None) -> None:
        self._legacy_image = value
        self._frame = None

    @property
    def has_display(self) -> bool:
        return self._frame is not None or self._legacy_image is not None

    @property
    def preparing(self) -> bool:
        return self._worker is not None and self._worker.preparing

    def display_pixel(self, x: int, y: int) -> QColor:
        if self._frame is not None:
            return self._frame.pixel_color(x, y)
        if self._legacy_image is not None:
            return self._legacy_image.pixelColor(x, y)
        return QColor(0, 0, 0, 0)

    def scene(self, scene, *, tiled_display=False):
        if self._worker is not None:
            self._worker.materialize_image = not tiled_display
        scene = self._vectors.prepare(scene)
        self._style_halos = style_halos(scene.flat_layers)
        native_layers = [layer for layer in scene.flat_layers if layer.native_cmyk is not None]
        if not native_layers or scene.channel_view is not None:
            self.pending = False
            return scene
        structure = (
            scene.width,
            scene.height,
            tuple(
                (
                    layer.id,
                    getattr(layer.native_cmyk, "_projection_identity", layer.native_cmyk),
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
                    repr(getattr(layer, "_preview_transform", None)),
                    style_signature(layer),
                )
                for layer in scene.flat_layers
            ),
        )
        bounds = QRect(0, 0, scene.width, scene.height)
        versions = {layer.id: getattr(layer.native_cmyk, "version", 0) for layer in native_layers}
        paint_damage = self._paint_damage.update(structure, scene)
        paint_damage = None if any(self._style_halos.values()) else paint_damage
        self._queue_scene_change(scene, native_layers, structure, bounds, paint_damage)
        self._observed_versions = versions
        self._dirty_queue.prioritize(
            QRect(x + layer.offset_x, y + layer.offset_y, width, height).intersected(bounds)
            for layer in native_layers
            for x, y, width, height in getattr(layer.native_cmyk, "priority_regions", ())
        )
        self.last_tile_count = 0
        self.pending = False
        published = QRect()
        if self._dirty_queue:
            if self._worker is not None:
                published = self._async_update(scene, native_layers, structure, versions)
            else:
                dirty = self._dirty_rects(native_layers, bounds)
                if self._structure != structure:
                    dirty = split_regions((bounds,))
                patches = self._sync_patches(scene, dirty)
                self._publish(patches)
                for rect, _patch in patches:
                    published = published.united(rect)
                self._structure = structure
                self._versions = versions
                self._dirty_queue.clear()
        elif self._structure == structure:
            self._versions = versions
        return self._scene(scene, published, tiled_display=tiled_display)

    def _queue_scene_change(self, scene, native_layers, structure, bounds, paint_damage):
        if self._requested_structure != structure:
            if self._style_only_change(self._requested_structure, structure):
                # Update every tile, retaining the last display outside each
                # completed batch. Never inherit pixels across pages/sources,
                # placement, masks, visibility, or ordinary layer blending.
                self._dirty_queue.clear()
                self._dirty_queue.merge((bounds,))
                self._inherit_contexts = tuple(
                    context
                    for context in (
                        self._structure,
                        self._requested_structure,
                        *self._inherit_contexts[:14],
                    )
                    if context is not None
                )
            elif self._worker is not None and paint_damage is not None:
                self._retain_paint_contexts()
                self._dirty_queue.merge(rect.intersected(bounds) for rect in paint_damage)
                self._dirty_queue.merge(
                    self._dirty_rects(native_layers, bounds, self._observed_versions)
                )
            else:
                self._dirty_queue.clear()
                self._dirty_queue.merge((bounds,))
                self._inherit_contexts = ()
            self._requested_structure = structure
            if self._display_size != (scene.width, scene.height):
                self._legacy_image = None
                self._frame = NativeCmykDisplayFrame(
                    (), None, None, surface=NativeCmykDisplayTiles(scene.width, scene.height)
                )
                self._display_size = scene.width, scene.height
        else:
            self._dirty_queue.merge(
                self._dirty_rects(native_layers, bounds, self._observed_versions)
            )

    @staticmethod
    def _style_only_change(previous, current):
        return (
            previous is not None
            and previous[:2] == current[:2]
            and tuple(layer[:-1] for layer in previous[2])
            == tuple(layer[:-1] for layer in current[2])
        )

    def _retain_paint_contexts(self):
        # Only these contexts can still own the worker's cached mosaic:
        # the last request, the published display, or the in-flight job.
        # Keep transitive paint compatibility without resetting the display
        # after a fixed number of edits or retaining an unbounded history.
        compatible = (self._requested_structure, *self._inherit_contexts)
        key = getattr(self._worker, "_key", None)
        running = key[0] if isinstance(key, tuple) else None
        retained = []
        for context in (self._requested_structure, self._structure, running):
            if context is not None and context in compatible and context not in retained:
                retained.append(context)
        self._inherit_contexts = tuple(retained)

    def _async_update(self, scene, native_layers, structure, versions):
        dirty = self._dirty_queue.batch(native_layers)
        key = (structure, tuple(versions.items()), self._dirty_queue.serial)
        options = {"inherit_contexts": self._inherit_contexts} if self._inherit_contexts else {}
        frame = self._worker.patches(scene, dirty, key, allow_older=True, **options)
        published = QRect()
        if frame is not None:
            self._publish(frame)
            for rect, _rgba in frame:
                published = published.united(QRect(*rect))
            self._structure = structure
            self._inherit_contexts = ()
            self._versions = dict(frame.key[1])
            self._dirty_queue.acknowledge(frame)
            if self._dirty_queue:
                remaining = self._dirty_queue.batch(native_layers)
                options = (
                    {"inherit_contexts": self._inherit_contexts} if self._inherit_contexts else {}
                )
                self._worker.patches(scene, remaining, key, allow_older=True, **options)
        self.pending = bool(self._dirty_queue)
        return published

    def _sync_patches(self, scene, dirty):
        if self._compositor is None:
            self._compositor = NativeCmykCompositor(self._manager)
        patches = []
        regions = split_regions((dirty,)) if isinstance(dirty, QRect) else dirty
        for region in regions:
            rect = QRect(*region)
            pixels = self._compositor.tile(scene, rect)
            patches.append((rect, ChannelPixelPolicy.image(pixels.preview(), straight=True)))
        return patches

    def _dirty_rects(
        self, layers: Any, bounds: QRect, versions: dict[str, int] | None = None
    ) -> tuple[tuple[int, int, int, int], ...]:
        dirty = []
        for layer in layers:
            native = layer.native_cmyk
            revision = (self._versions if versions is None else versions).get(layer.id, -1)
            getter = getattr(native, "dirty_regions_since", getattr(native, "dirty_since", None))
            changes = getter(revision) if getter is not None else (() if revision == 0 else None)
            if changes is None:
                return split_regions((bounds,))
            for x, y, width, height in changes:
                rect = document_rect(QRect(x, y, width, height), placement(layer))
                halo = getattr(self, "_style_halos", {}).get(layer.id, 0)
                rect = rect.adjusted(-halo, -halo, halo, halo)
                rect = rect.intersected(bounds)
                if not rect.isEmpty():
                    dirty.append(rect)
        return split_regions(dirty)

    def _dirty_regions(self, layers, bounds):
        dirty = QRect()
        for region in self._dirty_rects(layers, bounds):
            dirty = dirty.united(QRect(*region))
        return dirty

    def _publish(self, patches):
        if isinstance(patches, NativeCmykDisplayFrame):
            self._frame = patches
            self._legacy_image = None
            self._version += 1
            self.last_tile_count = len(patches)
            return
        image = cast(QImage, self._image).copy()
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        for rect, patch in patches:
            painter.drawImage(rect.topLeft(), patch)
        painter.end()
        self._image = image
        self._version += 1
        self.last_tile_count = len(patches)

    def _scene(self, scene, dirty, *, tiled_display=False):
        frame = self._frame if tiled_display and self._frame is not None else None
        layer = RenderLayerSnapshot(
            "native-cmyk-projection",
            "CMYK",
            True,
            1.0,
            "normal",
            self._version,
            image=None if frame is not None else self._image,
            native_display=frame,
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
        self._display_size = None
        self._structure = None
        self._requested_structure = None
        self._versions.clear()
        self._observed_versions.clear()
        self._dirty_queue.clear()
        self._vectors = NativeCmykVectorScene()
        self._paint_damage = NativeCmykPaintDamage()
        self._inherit_contexts = ()
