"""GPU target descriptors and factories."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .runtime import GpuDeviceContext


@dataclass(frozen=True, slots=True)
class GpuTargetDescriptor:
    """Backend-neutral render target descriptor."""

    width: int
    height: int
    format: str = "rgba8unorm"
    kind: str = "offscreen"
    usage: tuple[str, ...] = ("render", "copy_src")
    label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GpuTargetHandle:
    """Backend target handle with a neutral descriptor."""

    descriptor: GpuTargetDescriptor
    texture: Any
    view: Any

    @property
    def width(self) -> int:
        return self.descriptor.width

    @property
    def height(self) -> int:
        return self.descriptor.height

    @property
    def format(self) -> str:
        return self.descriptor.format


class GpuTargetFactory:
    """Create common offscreen targets and future native-surface descriptors."""

    def __init__(self, context: GpuDeviceContext) -> None:
        self._context = context

    def create_offscreen_color_target(
        self,
        width: int,
        height: int,
        *,
        format: str = "rgba8unorm",
        label: str | None = None,
    ) -> GpuTargetHandle:
        descriptor = GpuTargetDescriptor(
            width=max(1, int(width)),
            height=max(1, int(height)),
            format=format,
            kind="offscreen",
            label=label or "loomstroke.offscreen.color",
        )
        texture = self._context.device.create_texture(
            size=(descriptor.width, descriptor.height, 1),
            dimension="2d",
            format=descriptor.format,
            usage=(
                self._context.wgpu.TextureUsage.RENDER_ATTACHMENT
                | self._context.wgpu.TextureUsage.COPY_SRC
                | self._context.wgpu.TextureUsage.TEXTURE_BINDING
            ),
        )
        return GpuTargetHandle(
            descriptor=descriptor,
            texture=texture,
            view=texture.create_view(),
        )

    def create_map_target(
        self,
        width: int,
        height: int,
        *,
        map_kind: str,
        format: str = "rgba8unorm",
    ) -> GpuTargetHandle:
        return self.create_offscreen_color_target(
            width,
            height,
            format=format,
            label=f"loomstroke.3d.{map_kind}.map",
        )

    @staticmethod
    def native_surface_descriptor(
        width: int,
        height: int,
        *,
        format: str = "rgba8unorm",
        label: str | None = None,
    ) -> GpuTargetDescriptor:
        """Return a descriptor used by future direct/native 2D surface work."""

        return GpuTargetDescriptor(
            width=max(1, int(width)),
            height=max(1, int(height)),
            format=format,
            kind="surface",
            usage=("render", "present"),
            label=label or "loomstroke.native.surface",
            metadata={"status": "descriptor-only"},
        )
