"""Reuse submitted intermediate targets on the same ordered GPU queue."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from .types import WgpuRenderTargetHandle


def allocate_render_target(bundle: Any, width: int, height: int) -> WgpuRenderTargetHandle:
    """Allocate a target without registering it with either scratch pool.

    Retained renderer caches own their targets across frames. They must never
    lease scratch also referenced by completed group ownership lists.
    """
    wgpu = bundle.wgpu
    texture = bundle.device.create_texture(
        size=(int(width), int(height), 1),
        usage=(
            wgpu.TextureUsage.RENDER_ATTACHMENT
            | wgpu.TextureUsage.COPY_SRC
            | wgpu.TextureUsage.COPY_DST
            | wgpu.TextureUsage.TEXTURE_BINDING
        ),
        format=wgpu.TextureFormat.rgba8unorm,
    )
    try:
        return WgpuRenderTargetHandle(
            texture=texture,
            view=texture.create_view(),
            width=int(width),
            height=int(height),
            format="rgba8unorm",
        )
    except Exception:
        texture.destroy()
        raise


def take_retired_target(
    batches: list[list[Any]], width: int, height: int, *, required_usage=None, required_format=None
):
    """Take an exact-size scratch target which is no longer an exported result.

    Earlier submitted commands precede all writes in the next submission on
    this compositor's queue. Taking ownership removes the target from retirement,
    so release/expiry cannot destroy it while the new frame owns it.
    """
    for batch in batches:
        for index, target in enumerate(batch):
            if required_usage is not None and not (int(target.texture.usage) & required_usage):
                continue
            if required_format is not None and target.format != required_format:
                continue
            if target.width == width and target.height == height:
                return batch.pop(index)
    return None


@contextmanager
def release_targets_on_error(compositor: Any, owned_targets: list[Any]):
    """Cover failures after a child pass returned but before its parent adopted it."""
    try:
        yield
    except Exception:
        for target in owned_targets:
            compositor._destroy_target(target)
        raise
