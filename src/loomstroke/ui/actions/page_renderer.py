"""Lazy offscreen page renderer shared by file-writing actions (wgpu, released after use)."""

from __future__ import annotations

from typing import Any

import numpy as np

__all__ = ["PageRenderer"]


class PageRenderer:
    def __init__(self) -> None:
        self._renderer: Any | None = None

    def render(self, metadata: Any, page: Any) -> np.ndarray:
        from loomstroke.ui.canvas.page_scene_snapshot import scene_from_page  # noqa: PLC0415
        from loomstroke.ui.canvas.wgpu_backend import WgpuOffscreenRenderer  # noqa: PLC0415

        if self._renderer is None:
            self._renderer = WgpuOffscreenRenderer()
        return np.ascontiguousarray(
            self._renderer.render_scene(scene_from_page(metadata, page)).rgba
        )

    def release(self) -> None:
        renderer, self._renderer = self._renderer, None
        release = getattr(renderer, "release", None)
        if callable(release):
            release()

    def __enter__(self) -> PageRenderer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
