"""Persistent display textures consume immutable native ICC preview tiles."""

from dataclasses import replace
from typing import Any

from PySide6.QtCore import QRect

from .types import WgpuTextureHandle, WgpuTextureUploadReport


class NativeCmykTileTextures:
    def __init__(self) -> None:
        self._states: dict[str, tuple[object, int]] = {}

    def forget(self, layer_id: str) -> tuple[object, int] | None:
        return self._states.pop(layer_id, None)

    def sync(
        self, owner: Any, layer: Any, invalidation: Any, convert: Any
    ) -> tuple[WgpuTextureHandle, WgpuTextureUploadReport, tuple[str, ...]]:
        surface = layer.native_display.surface
        layer_id = str(layer.id)
        version = int(layer.texture_version)
        previous = self._states.get(layer_id)
        since = previous[1] if previous is not None else -1
        existing = owner.get(layer_id)
        invalid = invalidation is not None and (
            invalidation.gpu_resources.all_resources
            or layer_id in invalidation.gpu_resources.layer_ids
        )
        reset = (
            invalid
            or existing is None
            or previous is None
            or previous[0] is not surface.family
            or previous[1] > surface.sequence
            or existing.width != surface.width
            or existing.height != surface.height
        )
        handle = existing
        if reset:
            handle = owner._create_layer_texture(
                layer_id=layer_id,
                width=surface.width,
                height=surface.height,
                texture_version=version,
                offset_x=layer.offset_x,
                offset_y=layer.offset_y,
            )
        changes = [
            (key, tile) for key, tile in surface.tiles.items() if reset or tile.revision > since
        ]
        try:
            for (tx, ty), tile in changes:
                rect = QRect(
                    tx * surface.SIZE, ty * surface.SIZE, tile.image.width(), tile.image.height()
                )
                owner._upload_partial_region(handle, convert(tile.image), rect, version)
        except BaseException:
            if reset:
                handle.texture.destroy()
            raise
        if reset and existing is not None:
            existing.texture.destroy()
        updated = replace(
            handle, texture_version=version, offset_x=layer.offset_x, offset_y=layer.offset_y
        )
        owner._textures[layer_id] = updated
        self._states[layer_id] = surface.family, surface.sequence
        return (
            updated,
            WgpuTextureUploadReport(
                layer_id,
                version,
                uploaded=reset or bool(changes),
                partial=not reset and bool(changes),
            ),
            (),
        )
