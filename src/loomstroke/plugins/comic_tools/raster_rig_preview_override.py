"""Transient raster-rig preview for the REAL compositor (runtime-only).

``TransientRasterRigPreview`` is attached to the canvas object as
``canvas._transient_raster_rig_preview`` while the prototype session is
open. ``render_scene.build_render_scene_snapshot`` asks it to rewrite the
layer snapshot tuple at DRAW time only:

* the target BODY layer snapshot is replaced by a virtual layer
  (``<id>#rigpreview``) whose image is the body in DOCUMENT space with the
  current opening applied (transform / mask already baked into the capture,
  opacity / blend / visibility / clipping carried over unchanged);
* a virtual TAIL layer (``<id>#rigtail``) holding the posed tail patch at
  its document rect is inserted right above (``tail_over``) or below
  (``tail_under``) the body with the body's opacity and blend mode.

Nothing in the document model, the layer pixels, the dirty state or the
history is touched; removing the attribute restores the original scene.
The preview body image is rebuilt from the composer every frame inside the
dirty rect only and uploaded as a PARTIAL texture update
(``RenderLayerSnapshot.preview_dirty_rect``); the tail patch is a small
texture uploaded whole. Revisions let diagnostics detect stale frames.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

PREVIEW_SUFFIX = "#rigpreview"
TAIL_SUFFIX = "#rigtail"


@dataclass
class TransientRasterRigPreview:
    body_layer_id: str
    canvas_size: tuple[int, int]
    z_order: str = "tail_over"
    #: canvas-size RGBA8888 buffer backing the preview body QImage
    body_buf: np.ndarray = field(init=False, repr=False)
    body_image: Any = field(init=False, repr=False)
    body_version: int = field(default=1, init=False)
    body_dirty: tuple[int, int, int, int] | None = field(default=None, init=False)
    tail_image: Any = field(default=None, init=False, repr=False)
    tail_rect: tuple[int, int, int, int] | None = field(default=None, init=False)
    tail_version: int = field(default=1, init=False)
    revision: int = field(default=0, init=False)
    applied_count: int = field(default=0, init=False)
    #: upload accounting (diagnostics): body partial region px, tail bytes
    last_body_region_px: int = field(default=0, init=False)
    last_tail_bytes: int = field(default=0, init=False)
    body_full_uploads: int = field(default=0, init=False)
    #: Committed balloons use document pixels outside an active drag.
    enabled: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        from PySide6.QtGui import QImage  # noqa: PLC0415

        w, h = self.canvas_size
        self.body_buf = np.zeros((h, w, 4), np.uint8)
        self.body_image = QImage(self.body_buf.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
        self.body_full_uploads = 1  # the first sync of the virtual texture

    # ── updates from the session ─────────────────────────────────────
    def set_body_full(self, rgba: np.ndarray) -> None:
        """Initial body content (document-space canvas-size RGBA)."""

        h, w = self.body_buf.shape[:2]
        self.body_buf[:, :] = rgba[:h, :w]
        self.body_version += 1
        self.body_dirty = None  # full upload (first sync only)
        self.revision += 1

    def update_body_region(self, rect: tuple[int, int, int, int], rgba: np.ndarray) -> None:
        """Replace the body pixels inside ``rect`` (document px)."""

        x, y, w, h = rect
        self.body_buf[y : y + h, x : x + w] = rgba
        self.body_version += 1
        self.body_dirty = rect
        self.last_body_region_px = w * h
        self.revision += 1

    def set_tail(self, rect: tuple[int, int, int, int] | None, rgba: np.ndarray | None) -> None:
        from PySide6.QtGui import QImage  # noqa: PLC0415

        if rect is None or rgba is None or rgba.size == 0:
            self.tail_image = None
            self.tail_rect = None
            self.last_tail_bytes = 0
        else:
            x, y, w, h = rect
            buf = np.ascontiguousarray(rgba)
            self._tail_buf = buf  # keep alive behind the QImage
            self.tail_image = QImage(buf.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
            self.tail_rect = (x, y, w, h)
            self.last_tail_bytes = int(buf.nbytes)
        self.tail_version += 1
        self.revision += 1

    # ── draw-time hook (called by render_scene) ──────────────────────
    def apply_to_layers(self, layers: tuple[Any, ...]) -> tuple[Any, ...]:
        if not self.enabled:
            return layers
        out, found = self._apply(list(layers))
        if found:
            self.applied_count += 1
        return tuple(out)

    def _apply(self, layers: list[Any]) -> tuple[list[Any], bool]:
        result: list[Any] = []
        found = False
        for snap in layers:
            if str(getattr(snap, "id", "")) == self.body_layer_id:
                found = True
                body = replace(
                    snap,
                    id=f"{snap.id}{PREVIEW_SUFFIX}",
                    image=self.body_image,
                    offset_x=0,
                    offset_y=0,
                    transform=None,
                    _preview_transform=None,
                    _preview_transform_version=0,
                    layer_mask=None,  # baked into the capture
                    texture_version=self.body_version,
                    preview_dirty_rect=self.body_dirty,
                    vector_raster_scale=1,
                    vector_raster_origin=(0, 0),
                    vector_render_layer=None,
                    vector_direct_ready=False,
                    children=[],
                )
                tail = None
                if self.tail_image is not None and self.tail_rect is not None:
                    x, y, _w, _h = self.tail_rect
                    tail = replace(
                        body,
                        id=f"{snap.id}{TAIL_SUFFIX}",
                        name=f"{getattr(snap, 'name', '')} (tail preview)",
                        image=self.tail_image,
                        offset_x=int(x),
                        offset_y=int(y),
                        texture_version=self.tail_version,
                        preview_dirty_rect=None,
                        clipping=False,
                        effect_type=None,
                        effect_params={},
                        effect_mask=None,
                    )
                if tail is not None and self.z_order == "tail_under":
                    result.append(tail)
                    result.append(body)
                else:
                    result.append(body)
                    if tail is not None:
                        result.append(tail)
                continue
            children = list(getattr(snap, "children", None) or [])
            item = snap
            if children:
                new_children, sub_found = self._apply(children)
                if sub_found:
                    found = True
                    item = replace(snap, children=new_children)
            result.append(item)
        return result, found


def install(canvas: Any, preview: TransientRasterRigPreview | None) -> None:
    """Attach / detach the transient preview on the canvas object."""

    if canvas is None:
        return
    if preview is None:
        if hasattr(canvas, "_transient_raster_rig_preview"):
            try:
                delattr(canvas, "_transient_raster_rig_preview")
            except Exception:
                canvas._transient_raster_rig_preview = None  # noqa: SLF001
    else:
        canvas._transient_raster_rig_preview = preview  # noqa: SLF001


__all__ = ["PREVIEW_SUFFIX", "TAIL_SUFFIX", "TransientRasterRigPreview", "install"]
