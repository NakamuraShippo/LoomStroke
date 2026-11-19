"""Texture cache and upload path for the optional wgpu backend."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.canvas_invalidation import RenderInvalidation

from .initial_texture_upload import upload_initial_or_full
from .native_cmyk_tile_textures import NativeCmykTileTextures
from .paint_tile_upload import paint_image_key, sync_paint_tiles
from .qimage_region_converter import WgpuQImageRegionConverter
from .types import (
    WgpuDeviceBundle,
    WgpuTextureError,
    WgpuTextureHandle,
    WgpuTextureUploadReport,
)


class WgpuTextureManager:
    """Cache layer textures by ``layer_id`` and ``texture_version``."""

    def __init__(self, bundle: WgpuDeviceBundle, *, texture_format: str = "rgba8unorm") -> None:
        self._bundle = bundle
        self._wgpu = bundle.wgpu
        self._device = bundle.device
        self._queue = bundle.queue
        self.texture_format = texture_format
        self._textures: dict[str, WgpuTextureHandle] = {}
        self._region_converter = WgpuQImageRegionConverter()
        self._native_tiles = NativeCmykTileTextures()

    def get(self, layer_id: str) -> WgpuTextureHandle | None:
        return self._textures.get(layer_id)

    def remove(self, layer_id: str) -> None:
        self._native_tiles.forget(layer_id)
        handle = self._textures.pop(layer_id, None)
        if handle is not None and hasattr(handle.texture, "destroy"):
            handle.texture.destroy()

    def remove_stale(self, active_layer_ids: set[str]) -> None:
        for layer_id in set(self._textures) - active_layer_ids:
            self.remove(layer_id)

    def release(self) -> None:
        """Destroy all cached textures owned by this manager."""

        for layer_id in tuple(self._textures):
            self.remove(layer_id)

    def estimated_bytes(self) -> int:
        """Return a conservative cache-size estimate for diagnostics."""

        return sum(handle.width * handle.height * 4 for handle in self._textures.values())

    def sync_layer_texture(
        self,
        layer: Any,
        *,
        invalidation: RenderInvalidation | None = None,
        active_layer_id: str | None = None,
    ) -> tuple[WgpuTextureHandle, WgpuTextureUploadReport, tuple[str, ...]]:
        layer_id = str(getattr(layer, "id", ""))
        if getattr(layer, "native_display", None) is not None:
            return self._native_tiles.sync(self, layer, invalidation, qimage_to_straight_rgba)
        if self._native_tiles.forget(layer_id) is not None:
            self.remove(layer_id)
        image, width, height, version, offset_x, offset_y = self._raster_input(layer, layer_id)
        existing = self._textures.get(layer_id)
        resources_invalidated = invalidation is not None and (
            invalidation.gpu_resources.all_resources
            or layer_id in invalidation.gpu_resources.layer_ids
        )

        if existing is None or existing.width != width or existing.height != height:
            handle = self._create_layer_texture(
                layer_id=layer_id,
                width=width,
                height=height,
                texture_version=version,
                offset_x=offset_x,
                offset_y=offset_y,
            )
            return self._initialize_texture(layer, handle, existing)

        if getattr(layer, "paint_upload", None) is not None or existing.paint_display:
            return sync_paint_tiles(
                self, existing, layer, resources_invalidated, qimage_to_straight_rgba
            )

        if not resources_invalidated and existing.texture_version == version:
            self._textures[layer_id] = replace(existing, offset_x=offset_x, offset_y=offset_y)
            return (
                self._textures[layer_id],
                WgpuTextureUploadReport(
                    layer_id=layer_id,
                    texture_version=version,
                    uploaded=False,
                ),
                (),
            )

        handle = existing
        updated = replace(
            handle,
            texture_version=version,
            image_key=image.cacheKey(),
            offset_x=offset_x,
            offset_y=offset_y,
        )
        return self._update_raster_texture(
            layer, updated, invalidation, active_layer_id, resources_invalidated
        )

    def _update_raster_texture(
        self, layer, handle, invalidation, active_layer_id, resources_invalidated
    ):
        image, layer_id = layer.image, handle.layer_id
        width, height, version = handle.width, handle.height, handle.texture_version
        dirty_rect = invalidation.dirty_rect if invalidation is not None else None
        # transient preview layers carry their own texture-space dirty rect
        # (small per-frame region of an otherwise unchanged image)
        preview_rect = getattr(layer, "preview_dirty_rect", None)
        if preview_rect is not None:
            px, py, pw, ph = (int(v) for v in preview_rect)
            preview_qrect = QRect(px, py, pw, ph).intersected(QRect(0, 0, width, height))
            if not preview_qrect.isEmpty():
                converted_region = self._region_converter.convert_region(image, preview_qrect)
                if converted_region is not None:
                    region_rgba, region_rect = converted_region
                    report = self._upload_partial_region(handle, region_rgba, region_rect, version)
                    self._textures[layer_id] = handle
                    return handle, report, ()
        partial_rect = self._layer_upload_rect(layer, dirty_rect, (width, height))
        can_partial = (
            active_layer_id is not None
            and layer_id == active_layer_id
            and invalidation is not None
            and not invalidation.full_recompose
            # Backend resource invalidation (device regeneration etc.) is
            # independent of dirty pixels: the whole texture content must
            # be re-uploaded, so partial upload is excluded entirely.
            and not resources_invalidated
            and partial_rect is not None
            # Prescaled or tight-region vector/text rasters (view
            # compositing C2 / live-edit bypass) carry canvas-px dirty
            # rects that no longer map 1:1 to texels.
            and int(getattr(layer, "vector_raster_scale", 1) or 1) == 1
            and tuple(getattr(layer, "vector_raster_origin", (0, 0)) or (0, 0)) == (0, 0)
        )
        if can_partial and partial_rect is not None:
            # P0-02: version miss on a same-size dense raster with a safe
            # 1:1 dirty mapping converts only the clipped ROI.  A converter
            # miss falls back to the full conversion with the identical
            # partial-upload behaviour.
            converted_region = self._region_converter.convert_region(image, partial_rect)
            if converted_region is not None:
                region_rgba, region_rect = converted_region
                report = self._upload_partial_region(handle, region_rgba, region_rect, version)
            else:
                rgba = qimage_to_straight_rgba(image)
                report = self._upload_partial(handle, rgba, partial_rect, version)
            self._textures[layer_id] = handle
            return handle, report, ()

        rgba = qimage_to_straight_rgba(image)
        reason = (
            "dirty rect partial upload was unsafe; used full upload fallback"
            if dirty_rect is not None
            else None
        )
        report = self._upload_full(handle, rgba, version, fallback_reason=reason)
        self._textures[layer_id] = handle
        warnings = (f"layer {layer_id}: {reason}",) if reason else ()
        return handle, report, warnings

    def _initialize_texture(self, layer, handle, existing):
        paint = getattr(layer, "paint_upload", None) is not None
        handle = replace(handle, image_key=paint_image_key(layer), paint_display=paint)
        try:
            if paint:
                report = self._upload_full(
                    handle, qimage_to_straight_rgba(layer.image), handle.texture_version
                )
            else:
                report = upload_initial_or_full(
                    self, layer, handle, handle.texture_version, qimage_to_straight_rgba
                )
        except Exception:
            handle.texture.destroy()
            raise
        if existing is not None:
            existing.texture.destroy()
        self._textures[handle.layer_id] = handle
        return handle, report, ()

    @staticmethod
    def _raster_input(layer: Any, layer_id: str) -> tuple[QImage, int, int, int, int, int]:
        image = getattr(layer, "image", None)
        if image is None or image.isNull():
            raise WgpuTextureError(f"layer {layer_id!r} has no raster image")
        return (
            image,
            int(image.width()),
            int(image.height()),
            int(getattr(layer, "texture_version", 0)),
            int(getattr(layer, "offset_x", 0)),
            int(getattr(layer, "offset_y", 0)),
        )

    def _layer_upload_rect(self, layer, dirty_rect, texture_size):
        from loomstroke.core.command_layer_access import (  # noqa: PLC0415
            _layer_transform,
        )
        from loomstroke.core.raster_edit_geometry import (  # noqa: PLC0415
            RasterEditGeometry,
        )

        if dirty_rect is not None and getattr(layer, "layer_type", None) in {"raster", "ai_effect"}:
            transform = _layer_transform(layer)
            if transform.scale_x != 1 or transform.scale_y != 1 or transform.rotation != 0:
                try:
                    return RasterEditGeometry.capture(layer).source_rect(dirty_rect)
                except ValueError:
                    return None
        return self._partial_upload_rect(
            dirty_rect,
            layer_offset=(int(getattr(layer, "offset_x", 0)), int(getattr(layer, "offset_y", 0))),
            texture_size=texture_size,
        )

    def _create_layer_texture(
        self,
        *,
        layer_id: str,
        width: int,
        height: int,
        texture_version: int,
        offset_x: int,
        offset_y: int,
    ) -> WgpuTextureHandle:
        usage = (
            self._wgpu.TextureUsage.TEXTURE_BINDING
            | self._wgpu.TextureUsage.COPY_DST
            | self._wgpu.TextureUsage.COPY_SRC
        )
        texture = self._device.create_texture(
            size=(width, height, 1),
            usage=usage,
            format=self._wgpu.TextureFormat.rgba8unorm,
        )
        return WgpuTextureHandle(
            texture=texture,
            view=texture.create_view(),
            width=width,
            height=height,
            layer_id=layer_id,
            texture_version=texture_version,
            offset_x=offset_x,
            offset_y=offset_y,
        )

    def _upload_full(
        self,
        handle: WgpuTextureHandle,
        rgba: np.ndarray,
        texture_version: int,
        *,
        fallback_reason: str | None = None,
    ) -> WgpuTextureUploadReport:
        data = np.ascontiguousarray(rgba)
        self._queue.write_texture(
            {"texture": handle.texture, "origin": (0, 0, 0)},
            data,
            {"bytes_per_row": handle.width * 4, "rows_per_image": handle.height},
            (handle.width, handle.height, 1),
        )
        return WgpuTextureUploadReport(
            layer_id=str(handle.layer_id),
            texture_version=texture_version,
            uploaded=True,
            partial=False,
            fallback_full_upload=fallback_reason is not None,
            reason=fallback_reason,
        )

    def _upload_partial_region(
        self,
        handle: WgpuTextureHandle,
        region_rgba: np.ndarray,
        rect: QRect,
        texture_version: int,
    ) -> WgpuTextureUploadReport:
        """Upload an already ROI-converted array at ``rect`` (P0-02)."""

        data = np.ascontiguousarray(region_rgba)
        self._queue.write_texture(
            {"texture": handle.texture, "origin": (rect.x(), rect.y(), 0)},
            data,
            {"bytes_per_row": rect.width() * 4, "rows_per_image": rect.height()},
            (rect.width(), rect.height(), 1),
        )
        return WgpuTextureUploadReport(
            layer_id=str(handle.layer_id),
            texture_version=texture_version,
            uploaded=True,
            partial=True,
        )

    def _upload_partial(
        self,
        handle: WgpuTextureHandle,
        rgba: np.ndarray,
        rect: QRect,
        texture_version: int,
    ) -> WgpuTextureUploadReport:
        x, y, width, height = rect.x(), rect.y(), rect.width(), rect.height()
        sub = np.ascontiguousarray(rgba[y : y + height, x : x + width])
        self._queue.write_texture(
            {"texture": handle.texture, "origin": (x, y, 0)},
            sub,
            {"bytes_per_row": width * 4, "rows_per_image": height},
            (width, height, 1),
        )
        return WgpuTextureUploadReport(
            layer_id=str(handle.layer_id),
            texture_version=texture_version,
            uploaded=True,
            partial=True,
        )

    @staticmethod
    def _partial_upload_rect(
        dirty_rect: QRect | None,
        *,
        layer_offset: tuple[int, int],
        texture_size: tuple[int, int],
    ) -> QRect | None:
        if dirty_rect is None or dirty_rect.isEmpty():
            return None
        local = QRect(dirty_rect)
        local.translate(-layer_offset[0], -layer_offset[1])
        bounds = QRect(0, 0, texture_size[0], texture_size[1])
        clipped = local.intersected(bounds)
        if clipped.isEmpty():
            return None
        return clipped


def qimage_to_straight_rgba(image: QImage) -> np.ndarray:
    """Copy a QImage into straight-alpha RGBA8 numpy storage."""

    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width = converted.width()
    height = converted.height()
    row_bytes = converted.bytesPerLine()
    raw = np.frombuffer(converted.constBits(), dtype=np.uint8).reshape(height, row_bytes)
    return raw[:, : width * 4].reshape(height, width, 4).copy()
