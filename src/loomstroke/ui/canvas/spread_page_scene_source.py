"""Render scenes for a page that is not the active one (CT-06 B4-c3).

The spread viewport draws two pages, but ``build_render_scene_snapshot``
needs a canvas and the application has exactly one, holding the page being
edited.  This module supplies the missing half: a private, read-only
``SimpleCanvasService`` that a ``PageSnapshot`` is loaded into purely so a
scene can be built from it (spec §6.3.1).

What is *not* duplicated matters as much as what is.  The GPU device,
compositor and texture manager stay owned by the presenter and are shared;
only CPU-side layer state is duplicated here.  That is the same ownership
contract ``wgpu_native_preview_renderer`` documents, and the reason a
second full canvas widget was rejected.

The preview canvas is deliberately inert: no ``EventBus`` is attached, so
loading a page cannot emit ``DOC_CHANGED`` and cannot mark anything dirty.
Nothing here writes back to the document.

Caching is a single slot, by design.  ``RenderLayerSnapshot.from_layer``
*references* each layer's ``QImage`` rather than copying it, so a scene
cached from an earlier page would alias images that a later
``replace_layers`` has already dropped.  Holding one page at a time makes
that impossible, and a spread never needs more than one inactive page.

Failures return ``None`` instead of raising: a preview that cannot be built
must not stop the user editing the active page (spec §13).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QColor

from loomstroke.core.canvas_service_simple import SimpleCanvasService
from loomstroke.core.document_snapshot_builder import DocumentSnapshotBuilder
from loomstroke.host_api import CanvasSpec
from loomstroke.ui.canvas.render_scene import build_render_scene_snapshot

if TYPE_CHECKING:  # pragma: no cover - typing only
    from loomstroke.io.layer_io import PageSnapshot
    from loomstroke.ui.canvas.render_scene import RenderSceneSnapshot

logger = logging.getLogger(__name__)

__all__ = ["SpreadPageScene", "SpreadPageSceneSource"]


@dataclass(frozen=True, slots=True)
class SpreadPageScene:
    """A built scene plus the canvas size it was composed for."""

    page_id: str
    scene: RenderSceneSnapshot
    width: int
    height: int


class SpreadPageSceneSource:
    """Builds and caches the render scene for one inactive page.

    Callers must invalidate when the page's content changes -- this class
    cannot observe edits, because its canvas is not the one being edited.
    In practice a page only changes while it is *active*, so invalidating
    on page switch and on document reload is enough.
    """

    def __init__(self) -> None:
        self._canvas: SimpleCanvasService | None = None
        self._cached: SpreadPageScene | None = None

    # -- public API ----------------------------------------------------

    def scene_for_page(
        self,
        page: PageSnapshot,
        *,
        fallback_canvas_spec: CanvasSpec,
    ) -> SpreadPageScene | None:
        """Scene for ``page``, rebuilding only when the cached page differs.

        Returns ``None`` if the scene could not be built; the caller should
        keep rendering the active page and skip the inactive side.
        """
        page_id = str(getattr(page, "id", "") or "")
        if not page_id:
            logger.warning("spread scene: page has no id; skipping preview")
            return None

        cached = self._cached
        if cached is not None and cached.page_id == page_id:
            return cached

        spec = self._resolve_spec(page, fallback_canvas_spec)
        try:
            canvas = self._ensure_canvas(spec)
            self._load_page(canvas, page, spec)
            scene = build_render_scene_snapshot(
                canvas,
                rasterize_vector_layers=True,
                # The inactive page is read-only: it has no live selection
                # or in-progress stroke to overlay.
                include_selection_preview=False,
            )
        except Exception as exc:  # noqa: BLE001 - preview must never break editing
            logger.warning("spread scene: failed to build preview for %s: %s", page_id, exc)
            self._cached = None
            return None

        built = SpreadPageScene(
            page_id=page_id,
            scene=scene,
            width=int(spec.width),
            height=int(spec.height),
        )
        self._cached = built
        return built

    def invalidate(self, page_id: str | None = None) -> None:
        """Drop the cached scene.

        With ``page_id`` the cache is dropped only when it holds that page,
        so an unrelated page's edit does not throw away a still-valid
        preview.
        """
        if self._cached is None:
            return
        if page_id is None or self._cached.page_id == page_id:
            self._cached = None

    def release(self) -> None:
        """Drop the cache and the preview canvas.

        Called when the spread view closes, so the inactive page's layer
        images stop being retained.
        """
        self._cached = None
        self._canvas = None

    @property
    def cached_page_id(self) -> str | None:
        return self._cached.page_id if self._cached is not None else None

    # -- internals -----------------------------------------------------

    def _resolve_spec(self, page: PageSnapshot, fallback: CanvasSpec) -> CanvasSpec:
        spec: CanvasSpec | None = getattr(page, "canvas", None)
        if spec is None:
            return fallback
        if int(getattr(spec, "width", 0)) <= 0 or int(getattr(spec, "height", 0)) <= 0:
            logger.warning("spread scene: page %s has a degenerate canvas spec", page.id)
            return fallback
        return spec

    def _ensure_canvas(self, spec: CanvasSpec) -> SimpleCanvasService:
        canvas = self._canvas
        if canvas is None:
            canvas = SimpleCanvasService(
                width=int(spec.width),
                height=int(spec.height),
                dpi=int(spec.dpi[0]),
                # Read-only: nothing here is ever undone.
                max_history_steps=1,
            )
            # No EventBus on purpose -- see the module docstring.
            self._canvas = canvas
        return canvas

    def _load_page(
        self,
        canvas: SimpleCanvasService,
        page: PageSnapshot,
        spec: CanvasSpec,
    ) -> None:
        # Convert before mutating the canvas: a conversion failure after the
        # resize would leave new geometry with the previous page's pixels.
        layers: list[Any] = [
            DocumentSnapshotBuilder.snapshot_to_simple_layer(snapshot)
            for snapshot in (page.layers or [])
        ]
        canvas.update_canvas_config(
            width=int(spec.width),
            height=int(spec.height),
            dpi=int(spec.dpi[0]),
        )
        background = spec.background_color
        canvas._background = QColor(
            int(background[0]),
            int(background[1]),
            int(background[2]),
            int(background[3]),
        )
        canvas.replace_layers(layers)
