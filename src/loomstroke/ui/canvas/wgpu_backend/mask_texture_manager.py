"""One-byte GPU coverage textures; document mask images remain unchanged."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from .mask_version_cache import MaskVersionCache
from .types import WgpuTextureHandle, WgpuTextureUploadReport


@dataclass(frozen=True, slots=True)
class WgpuMaskTextureHandle(WgpuTextureHandle):
    source_width: int = 0
    source_height: int = 0


class WgpuMaskTextureManager:
    """Cache canonical Alpha8 coverage by the established mask identity/version."""

    def __init__(self, bundle: Any) -> None:
        self._bundle = bundle
        self._textures: dict[str, WgpuMaskTextureHandle] = {}
        self._regions: dict[str, tuple[int, int, int, int]] = {}
        self._past = MaskVersionCache()

    def _restore_previous(self, mask_id, version, requested, source_size, regional):
        if not regional:
            return None
        restored = self._past.take((mask_id, version, requested, *source_size))
        if restored is None:
            return None
        existing = self._textures.get(mask_id)
        if existing is not None:
            self._past.remember(existing, self._regions[mask_id])
        self._textures[mask_id], self._regions[mask_id] = restored, requested
        return restored

    def _retire_previous(self, mask_id, existing, handle, retain):
        if existing is None or existing.texture is handle.texture:
            return
        if retain:
            self._past.remember(existing, self._regions[mask_id])
        else:
            existing.texture.destroy()

    def sync(self, mask_id: str, image: QImage, version: int, *, region=None):
        source_width, source_height = image.width(), image.height()
        left, top, width, height = region or (0, 0, source_width, source_height)
        if (
            min(left, top) < 0
            or min(width, height) <= 0
            or left + width > source_width
            or top + height > source_height
        ):
            raise ValueError("mask upload region is outside the source image")
        requested = left, top, width, height
        existing = self._textures.get(mask_id)
        restored = self._restore_previous(
            mask_id, version, requested, (source_width, source_height), region is not None
        )
        if restored is not None:
            return restored, WgpuTextureUploadReport(mask_id, version, uploaded=False)
        if (
            existing is not None
            and existing.texture_version == version
            and self._regions.get(mask_id) == requested
            and (existing.source_width, existing.source_height) == (source_width, source_height)
        ):
            return existing, WgpuTextureUploadReport(mask_id, version, uploaded=False)
        rows = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(
            source_height, image.bytesPerLine()
        )
        if region is not None:
            coverage = rows[top : top + height, left : left + width]
            active_y = np.flatnonzero(np.any(coverage, axis=1))
            active_x = np.flatnonzero(np.any(coverage, axis=0))
            if active_y.size and active_x.size:
                x0, y0 = max(0, int(active_x[0]) - 1), max(0, int(active_y[0]) - 1)
                x1 = min(width, int(active_x[-1]) + 2)
                y1 = min(height, int(active_y[-1]) + 2)
                left, top, width, height = left + x0, top + y0, x1 - x0, y1 - y0
            else:
                width = height = 1
        existing = self._textures.get(mask_id)
        retain = (
            region is not None
            and existing is not None
            and existing.texture_version != version
            and existing.width * existing.height <= self._past.budget_bytes
        )
        if (
            existing is not None
            and (existing.width, existing.height) == (width, height)
            and not retain
        ):
            if (
                existing.texture_version == version
                and (existing.offset_x, existing.offset_y) == (left, top)
                and (existing.source_width, existing.source_height) == (source_width, source_height)
            ):
                self._regions[mask_id] = requested
                return existing, WgpuTextureUploadReport(mask_id, version, uploaded=False)
            handle = replace(
                existing,
                texture_version=version,
                offset_x=left,
                offset_y=top,
                source_width=source_width,
                source_height=source_height,
            )
        else:
            wgpu = self._bundle.wgpu
            texture = self._bundle.device.create_texture(
                size=(width, height, 1),
                format=wgpu.TextureFormat.r8unorm,
                usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST,
            )
            try:
                handle = WgpuMaskTextureHandle(
                    texture,
                    texture.create_view(),
                    width,
                    height,
                    mask_id,
                    version,
                    left,
                    top,
                    source_width,
                    source_height,
                )
            except Exception:
                texture.destroy()
                raise
        try:
            data = np.ascontiguousarray(rows[top : top + height, left : left + width])
            self._bundle.queue.write_texture(
                {"texture": handle.texture, "origin": (0, 0, 0)},
                data,
                {"bytes_per_row": width, "rows_per_image": height},
                (width, height, 1),
            )
        except Exception:
            # A failed same-size upload may have changed some texels: discard it.
            handle.texture.destroy()
            self._textures.pop(mask_id, None)
            self._regions.pop(mask_id, None)
            if existing is not None and existing.texture is not handle.texture:
                existing.texture.destroy()
            raise
        self._retire_previous(mask_id, existing, handle, retain)
        self._textures[mask_id] = handle
        self._regions[mask_id] = requested
        return handle, WgpuTextureUploadReport(mask_id, version, uploaded=True)

    def remove_stale(self, active_ids: set[str]) -> None:
        self._past.remove_stale(active_ids)
        for mask_id in set(self._textures) - active_ids:
            self._textures.pop(mask_id).texture.destroy()
            self._regions.pop(mask_id, None)

    def release(self) -> None:
        self.remove_stale(set())
