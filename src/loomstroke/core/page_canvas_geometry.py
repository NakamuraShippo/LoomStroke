"""Install page geometry without resizing the outgoing page's live layers."""

from __future__ import annotations

from inspect import getattr_static
from typing import Any

from PySide6.QtGui import QColor

from loomstroke.host_api import CanvasSpec

from .canvas_composited_pixmap_state_service import CanvasCompositedPixmapStateService
from .canvas_service_simple_image_api import _DEFAULT_UPDATE_CANVAS_CONFIG


def _install_page_canvas_geometry(canvas: Any, spec: CanvasSpec) -> None:
    """Preserve custom configuration hooks; install standard page scalars only.

    Page replacement supplies a new layer tree. Authoring resize would first
    rewrite the old tree and publish a new edit, invalidating unrelated redo.
    Allocate the size-dependent pixmap before committing any geometry fields.
    """
    background = QColor(*spec.background_color)
    if getattr_static(canvas, "update_canvas_config", None) is not _DEFAULT_UPDATE_CANVAS_CONFIG:
        canvas.update_canvas_config(width=spec.width, height=spec.height, dpi=int(spec.dpi[0]))
        canvas._background = background
        return

    width, height, dpi = max(1, int(spec.width)), max(1, int(spec.height)), max(1, int(spec.dpi[0]))
    changed_size = (width, height) != (canvas._width, canvas._height)
    pixmap = (
        CanvasCompositedPixmapStateService.make_filled_pixmap(width, height, background)
        if changed_size
        else None
    )
    canvas._width, canvas._height, canvas._dpi = width, height, dpi
    canvas._background = background
    if pixmap is not None:
        canvas._selection_mask = None
        canvas._selection_bounds = None
        if getattr(canvas, "_vector_selection", None) is not None:
            canvas._vector_selection = None
        canvas._qpath_cache.clear()
        CanvasCompositedPixmapStateService.store_composited_pixmap(canvas, pixmap)
