"""Capability matrix of one document mode: what storage / tools / export support it today."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["ModeCapability"]


@dataclass(frozen=True, slots=True)
class ModeCapability:
    mode_id: str
    bits_per_channel: int
    channels: tuple[str, ...]
    alpha_supported: bool
    storage_supported: bool
    brush_supported: bool
    selection_mask_supported: bool
    composite_supported: bool
    export_supported: bool
    wgpu_texture_format: str
    notes: str = ""

    @property
    def channel_count(self) -> int:
        return len(self.channels)

    @property
    def bytes_per_pixel(self) -> float:
        return self.channel_count * self.bits_per_channel / 8.0

    @property
    def editable(self) -> bool:
        return self.storage_supported and self.brush_supported and self.composite_supported

    def to_dict(self) -> dict[str, Any]:
        return {
            "modeId": self.mode_id,
            "bitsPerChannel": self.bits_per_channel,
            "channels": list(self.channels),
            "alphaSupported": self.alpha_supported,
            "storageSupported": self.storage_supported,
            "brushSupported": self.brush_supported,
            "selectionMaskSupported": self.selection_mask_supported,
            "compositeSupported": self.composite_supported,
            "exportSupported": self.export_supported,
            "wgpuTextureFormat": self.wgpu_texture_format,
            "editable": self.editable,
            "notes": self.notes,
        }
