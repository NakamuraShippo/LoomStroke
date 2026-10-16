"""Raster-owned capture and atomic replacement for one editable pen stroke."""

from __future__ import annotations

import copy
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from ..commands import StampBrushCommand, _load_tip_mask
from ..dab_variation import DabVariation
from .geometry import Anchor, Curve, fit
from .preimage import Preimage, check_size
from .raster import command_bounds, crop, paste, render

logger = logging.getLogger(__name__)
_FITTER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="recontour-fit")


def _fit_curves(positions: dict, smoothness: float) -> dict[int, Curve]:
    return {lane: fit(tuple(points), smoothness) for lane, points in positions.items()}


class RasterStrokeSession:
    """Core owns pixels; callers only receive geometry and command results."""

    def __init__(self, smoothness: float) -> None:
        self.smoothness = smoothness
        self.commands: list[tuple[int, Any]] = []
        self.curves: dict[int, Curve] = {}
        self.edited: dict[int, tuple[Anchor, ...]] = {}
        self.rect = QRect()
        self.before = QImage()
        self.original = QImage()
        self.original_rect = QRect()
        self.tips: dict = {}
        self.tip_bytes = 0
        self.layer: Any = None
        self.canvas: Any = None
        self.mask = False
        self.expected: tuple = ()
        self.error: str | None = None
        self.closed = False
        self.ready = False
        self.capture = Preimage()
        self.future: Future | None = None

    def close(self) -> None:
        self.closed = True
        self.commands.clear()
        self.tips.clear()
        self.before = QImage()
        self.original = QImage()
        self.capture = Preimage()
        if self.future is not None:
            self.future.cancel()
            self.future = None

    def _signature(self, canvas: Any) -> tuple:
        layer = canvas._get_active_layer()
        if layer is None or layer.image is None:
            return ()
        image = layer.layer_mask if self.mask else layer.image
        selection = getattr(canvas, "_selection_mask", None)
        return (
            id(canvas),
            id(layer),
            image.cacheKey() if image is not None else None,
            bool(layer.mask_edit_mode),
            bool(layer.alpha_lock),
            bool(layer.locked),
            layer.offset_x,
            layer.offset_y,
            repr(layer.transform),
            selection.cacheKey() if selection is not None else None,
            repr(getattr(canvas, "_selection_bounds", None)),
            canvas._width,
            canvas._height,
        )

    def valid(self, canvas: Any) -> bool:
        return (
            not self.closed
            and self.error is None
            and canvas is self.canvas
            and bool(self.expected)
            and self.expected == self._signature(canvas)
        )

    def _extend(self, canvas: Any, rect: QRect) -> None:
        union = self.rect.united(rect) if not self.rect.isEmpty() else QRect(rect)
        check_size(union)
        if union == self.rect:
            return
        before = crop(self.layer, union, mask=self.mask)
        if not self.rect.isEmpty():
            paste(before, self.before, self.rect.x() - union.x(), self.rect.y() - union.y())
        self.before, self.rect = before, union

    def capture_before(self, canvas: Any, cmd: Any) -> None:
        if self.closed or self.error:
            return
        layer = canvas._get_active_layer()
        if self.layer is None:
            if layer is None or layer.image is None or layer.locked:
                self.close()
                return
            self.mask = bool(layer.mask_edit_mode and layer.layer_mask is not None)
            if layer.layer_type != "raster" and not self.mask:
                self.close()
                return
            self.canvas, self.layer = canvas, layer
        elif not self.valid(canvas):
            self.close()
            return
        rect = command_bounds(canvas, cmd)
        if self.mask:
            rect = QRect(
                layer.offset_x, layer.offset_y, layer.layer_mask.width(), layer.layer_mask.height()
            )
        if rect is not None:
            self.capture.capture(layer, rect, mask=self.mask)
            self.rect = QRect(self.capture.rect)
        if isinstance(cmd, StampBrushCommand):
            for point in cmd.points:
                size = DabVariation.size(cmd.brush, point)
                key = (cmd.tip_path, cmd.brush.hardness, size)
                if key not in self.tips:
                    if self.tip_bytes + size * size * 8 > 32 * 1024 * 1024:
                        raise ValueError("Recontour tip snapshot budget exceeded")
                    self.tips[key] = _load_tip_mask(
                        cmd.tip_path,
                        size,
                        cmd.brush.hardness,
                        getattr(cmd.brush, "hardness_mode", "radial"),
                    ).copy()
                    self.tip_bytes += self.tips[key].nbytes

    def capture_after(self, canvas: Any, lane: int, cmd: Any, applied: bool) -> None:
        if self.closed or self.error or self.layer is None:
            return
        if applied:
            frozen = copy.copy(cmd)
            frozen.points = list(cmd.points)
            self.commands.append((lane, frozen))
        self.expected = self._signature(canvas)

    def seal(self, canvas: Any) -> bool:
        if not self.valid(canvas) or not self.commands or self.rect.isEmpty():
            return False
        positions: dict[int, list] = {}
        for lane, cmd in self.commands:
            positions.setdefault(lane, []).extend((p.x, p.y) for p in cmd.points)
        self.original = crop(self.layer, self.rect, mask=self.mask)
        self.before = self.capture.materialize(self.layer, mask=self.mask)
        self.capture = Preimage()
        self.original_rect = QRect(self.rect)
        if sum(map(len, positions.values())) > 1024:
            self.future = _FITTER.submit(_fit_curves, positions, self.smoothness)
        else:
            self._set_curves(_fit_curves(positions, self.smoothness))
        return True

    def _set_curves(self, curves: dict[int, Curve]) -> None:
        self.curves = curves
        self.edited = {lane: curve.anchors for lane, curve in curves.items()}
        self.ready = True

    def poll(self, canvas: Any) -> bool:
        if not self.valid(canvas):
            return False
        if self.future is not None and self.future.done():
            try:
                self._set_curves(self.future.result())
                self.future = None
            except Exception:
                self.fail()
        return self.ready

    def _project_commands(self, edited: dict[int, tuple[Anchor, ...]]) -> list[Any]:
        positions = {lane: curve.project(edited[lane]) for lane, curve in self.curves.items()}
        cursors = dict.fromkeys(positions, 0)
        result = []
        for lane, cmd in self.commands:
            start = cursors[lane]
            mapped = positions[lane][start : start + len(cmd.points)]
            replay = copy.copy(cmd)
            replay.points = [
                replace(p, x=xy[0], y=xy[1]) for p, xy in zip(cmd.points, mapped, strict=True)
            ]
            result.append(replay)
            cursors[lane] += len(cmd.points)
        return result

    def commit(
        self, canvas: Any, edited: dict[int, tuple[Anchor, ...]], *, cancel: bool = False
    ) -> bool:
        if not self.ready or not self.valid(canvas):
            return False
        canvas.end_transaction()  # Flush the pen's deferred group before a distinct adjustment.
        if not self.valid(canvas):
            return False
        if not cancel and edited == self.edited:
            return True
        commands = self._project_commands(edited) if not cancel else []
        region = QRect(self.rect)
        for cmd in commands:
            bounds = command_bounds(canvas, cmd)
            if bounds is not None:
                region = region.united(bounds)
        self._extend(canvas, region)
        candidate = self.before.copy()
        base = {lane: c.anchors for lane, c in self.curves.items()}
        if cancel or edited == base:
            paste(
                candidate,
                self.original,
                self.original_rect.x() - self.rect.x(),
                self.original_rect.y() - self.rect.y(),
            )
        else:
            candidate = render(
                canvas,
                candidate,
                self.rect,
                commands,
                self.tips,
                alpha_lock=self.layer.alpha_lock,
                mask=self.mask,
            )
        previous = crop(self.layer, self.rect, mask=self.mask)
        if candidate == previous:
            self.edited = dict(edited)
            return True
        metadata = {"command": "recontour"}
        if self.mask:
            metadata["mask_edit"] = True
        patch = canvas._history_patch_from_images(
            self.layer.id, self.rect, previous, candidate, metadata
        )
        # All fallible rendering/patch construction precedes mutation of the live image.
        if not self.valid(canvas):
            return False
        if self.mask:
            paste(
                self.layer.layer_mask,
                candidate,
                self.rect.x() - self.layer.offset_x,
                self.rect.y() - self.layer.offset_y,
            )
            self.layer.filter_mask_version += 1
            canvas._gpu_mask_dirty_layers.add(self.layer.id)
        else:
            canvas._ensure_layer_image_bounds(self.layer, self.rect)
            paste(
                self.layer.image,
                candidate,
                self.rect.x() - self.layer.offset_x,
                self.rect.y() - self.layer.offset_y,
            )
            self.layer.texture_version += 1
        self.expected = self._signature(canvas)
        self.edited = dict(edited)
        canvas._record_history_patch("Recontour", patch)
        canvas._emit_layer_content_changed(self.layer, self.rect, metadata)
        canvas._recompose_region(self.rect)
        return True

    def fail(self) -> None:
        logger.exception("Recontour failed; raster stroke preserved")
        self.error = "Recontour could not edit this stroke. The raster stroke was preserved."
        self.close()
