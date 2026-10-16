"""Own current filter masks and defer replaced textures until frame submission."""

from __future__ import annotations

from typing import Any

import numpy as np

from .types import WgpuTextureHandle


class WgpuFilterMaskCache:
    """One mask per layer, plus bounded quantized opacity masks.

    A removed entry can still be referenced by the current command encoder.
    Only ``finish_frame`` (after submit or encoder abandonment) and ``release``
    destroy retired textures. The layer index makes replacement O(1).
    """

    def __init__(self, bundle: Any) -> None:
        self._bundle = bundle
        self.entries: dict[tuple[Any, ...], WgpuTextureHandle] = {}
        self._layer_keys: dict[str, tuple[Any, ...]] = {}
        self._retired: list[WgpuTextureHandle] = []

    def upload(
        self,
        key: tuple[Any, ...],
        rgba: np.ndarray,
        *,
        layer_id: str,
        version: int,
    ) -> WgpuTextureHandle:
        """Publish only after upload succeeds; failed uploads keep the old mask."""
        height, width = rgba.shape[:2]
        wgpu = self._bundle.wgpu
        texture = self._bundle.device.create_texture(
            size=(width, height, 1),
            usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST,
            format=wgpu.TextureFormat.rgba8unorm,
        )
        try:
            handle = WgpuTextureHandle(
                texture=texture,
                view=texture.create_view(),
                width=width,
                height=height,
                layer_id=layer_id,
                texture_version=version,
            )
            self._bundle.queue.write_texture(
                {"texture": texture, "origin": (0, 0, 0)},
                rgba,
                {"bytes_per_row": width * 4, "rows_per_image": height},
                (width, height, 1),
            )
        except Exception:
            texture.destroy()
            raise
        # Layer masks use four-part identity keys; constants use two parts.
        if len(key) == 4:
            owner = str(key[0])
            previous_key = self._layer_keys.get(owner)
            if previous_key is not None:
                self._retired.append(self.entries.pop(previous_key))
            self._layer_keys[owner] = key
        self.entries[key] = handle
        return handle

    def invalidate(self, layer_id: str | None = None) -> None:
        if layer_id is None:
            self._retired.extend(self.entries.values())
            self.entries.clear()
            self._layer_keys.clear()
            return
        key = self._layer_keys.pop(layer_id, None)
        if key is not None:
            self._retired.append(self.entries.pop(key))

    def finish_frame(self, active_layer_ids: set[str]) -> None:
        for layer_id in self._layer_keys.keys() - active_layer_ids:
            self.invalidate(layer_id)
        self._destroy_retired()

    def release(self) -> None:
        self.invalidate()
        self._destroy_retired()

    def _destroy_retired(self) -> None:
        retired, self._retired = self._retired, []
        for handle in retired:
            handle.texture.destroy()
