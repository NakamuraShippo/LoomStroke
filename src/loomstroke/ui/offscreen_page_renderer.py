"""Lazy wgpu offscreen page renderer shared by export / preview code paths.

Creates the :class:`WgpuOffscreenRenderer` on first use and renders a page
snapshot through the shared ``scene_from_page`` conversion (the same path
MCP batch render and File > Export Pages use), so every consumer sees the
same pixels.  ``release()`` frees the device resources.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..io.layer_io import DocumentMetadata

__all__ = ["OffscreenPageRenderer"]


class OffscreenPageRenderer:
    """``render(metadata, page) -> straight-alpha RGBA8`` with lazy setup."""

    def __init__(self) -> None:
        self._renderer: Any | None = None

    def render(self, metadata: DocumentMetadata, page: Any) -> np.ndarray:
        from .canvas.page_scene_snapshot import scene_from_page  # noqa: PLC0415

        if self._renderer is None:
            from .canvas.wgpu_backend import WgpuOffscreenRenderer  # noqa: PLC0415

            self._renderer = WgpuOffscreenRenderer()
        return self._renderer.render_scene(scene_from_page(metadata, page)).rgba

    def release(self) -> None:
        renderer, self._renderer = self._renderer, None
        if renderer is not None:
            renderer.release()
