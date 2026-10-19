"""Capture a live pen stroke and replace only the region changed by its tail."""

from __future__ import annotations

import copy
import logging
import math
from dataclasses import replace
from typing import Any, cast

from PySide6.QtCore import QRect

from .canvas_render_invalidation_service import CanvasRenderInvalidationService
from .commands import StampBrushCommand, _load_tip_mask
from .dab_variation import DabVariation
from .native_cmyk.stroke_image import NativeStrokeImage, commit_native_image
from .native_cmyk.tool_space import clip_replay_region
from .pen_taper_model import TaperStrokePoint, taper_scale
from .recontour.preimage import TILE, check_size
from .recontour.raster import (
    command_bounds,
    crop,
    paste,
    render,
    stroke_pixel_bytes,
    submit_live_stroke,
)
from .recontour.session import RasterStrokeSession

logger = logging.getLogger(__name__)


class PenTaperSession(RasterStrokeSession):
    """Reuse first-touch preimages without fitting geometry or changing Undo boundaries."""

    def __init__(self, start: float, end: float) -> None:
        super().__init__(0.0)
        self.start_length = start
        self.end_length = end
        self.lanes: dict[int, tuple[tuple[float, float], float]] = {}
        self.last_finish_pixels = 0
        self.last_finish_commands = 0
        self.entry_commands: dict[int, Any] = {}

    def capture_before(self, canvas: Any, cmd: Any) -> None:
        if self.end_length > 0.0:
            super().capture_before(canvas, cmd)
            return
        if self.closed or self.error:
            return
        if self.layer is None:
            layer = canvas._get_active_layer()
            if layer is None or layer.image is None or layer.locked:
                self.close()
                return
            self.canvas, self.layer = canvas, layer
            self.mask = bool(layer.mask_edit_mode and layer.layer_mask is not None)
        elif not self.valid(canvas):
            self.close()

    def capture_after(self, canvas: Any, lane: int, cmd: Any, applied: bool) -> None:
        if self.end_length > 0.0:
            super().capture_after(canvas, lane, cmd, applied)
            return
        if not self.closed and not self.error and self.layer is not None:
            if applied:
                self.entry_commands[lane] = cmd
            self.expected = self._signature(canvas)

    def prepare(self, command: Any, lane: int) -> Any:
        cmd = copy.copy(command)
        previous, distance = self.lanes.get(lane, ((command.points[0].x, command.points[0].y), 0.0))
        points = []
        for point in command.points:
            position = (point.x, point.y)
            distance += math.hypot(position[0] - previous[0], position[1] - previous[1])
            taper_point = (
                point
                if isinstance(point, TaperStrokePoint)
                else TaperStrokePoint(
                    x=point.x,
                    y=point.y,
                    pressure=point.pressure,
                    tilt_x=point.tilt_x,
                    tilt_y=point.tilt_y,
                    rotation=point.rotation,
                    timestamp=point.timestamp,
                )
            )
            points.append(
                replace(
                    taper_point,
                    distance=distance,
                    width_scale=taper_scale(distance, None, self.start_length, 0.0),
                )
            )
            previous = position
        self.lanes[lane] = (previous, distance)
        cmd.points = points
        return cmd

    def _final_command(self, lane: int, command: Any) -> Any:
        cmd = copy.copy(command)
        total = self.lanes[lane][1]
        cmd.points = [
            replace(
                point,
                width_scale=taper_scale(point.distance, total, self.start_length, self.end_length),
            )
            for point in command.points
        ]
        return cmd

    def _before_region(self, region: QRect) -> Any:
        if self.mask:
            return self.capture.mask.copy(region.translated(-self.capture.rect.topLeft()))
        before = crop(self.layer, region)
        for (x, y), tile in self.capture.tiles.items():
            tile_rect = QRect(x * TILE, y * TILE, TILE, TILE)
            overlap = region.intersected(tile_rect)
            if not overlap.isEmpty():
                paste(
                    before,
                    tile.copy(overlap.translated(-tile_rect.topLeft())),
                    overlap.x() - region.x(),
                    overlap.y() - region.y(),
                )
        return before

    def _snapshot_final_tips(self, record: Any, commands: list[tuple[int, Any]]) -> None:
        seen: set[tuple] = set()
        for _lane, cmd in commands:
            if not isinstance(cmd, StampBrushCommand):
                continue
            for point in cmd.points:
                size = DabVariation.size(cmd.brush, point)
                key = (cmd.tip_path, cmd.brush.hardness, size)
                if key in seen:
                    continue
                seen.add(key)
                if key not in record.tips or cmd.hardness_mode != "radial":
                    additional = 0 if key in record.tips else size * size * 8
                    if record.tip_bytes + additional > 32 * 1024 * 1024:
                        raise ValueError("Pen taper tip snapshot budget exceeded")
                    record.tips[key] = _load_tip_mask(
                        cmd.tip_path, size, cmd.brush.hardness, cmd.hardness_mode
                    ).copy()
                    if additional:
                        record.tip_bytes += record.tips[key].nbytes

    def finish(self, canvas: Any, record: Any = None) -> bool:
        try:
            if self.end_length <= 0.0:
                return self._finish_entry(canvas, record)
            if not self.valid(canvas) or not self.commands:
                return False
            final = [(lane, self._final_command(lane, cmd)) for lane, cmd in self.commands]
            region = QRect()
            for (_lane, original), (_final_lane, cmd) in zip(self.commands, final, strict=True):
                for index, (old, new) in enumerate(zip(original.points, cmd.points, strict=True)):
                    if old.width_scale == new.width_scale:
                        continue
                    affected = copy.copy(cmd)
                    affected.points = original.points[max(0, index - 1) : index + 2]
                    bounds = command_bounds(canvas, affected)
                    if bounds is not None:
                        region = region.united(bounds) if not region.isEmpty() else QRect(bounds)
            if not region.isEmpty():
                clipped = clip_replay_region(canvas, self.layer, region)
                if clipped is not None:
                    region = clipped
                    check_size(
                        region, bytes_per_pixel=stroke_pixel_bytes(self.layer, mask=self.mask)
                    )
                    commands = [
                        cmd
                        for _lane, cmd in final
                        if (bounds := command_bounds(canvas, cmd)) is not None
                        and bounds.intersects(region)
                    ]
                    self.last_finish_pixels = region.width() * region.height()
                    self.last_finish_commands = len(commands)
                    self._snapshot_final_tips(self, final)
                    candidate = render(
                        canvas,
                        self._before_region(region),
                        region,
                        commands,
                        self.tips,
                        alpha_lock=self.layer.alpha_lock,
                        mask=self.mask,
                    )
                    if record is not None and not record.closed and record.error is None:
                        self._snapshot_final_tips(record, final)
                    self._commit_region(canvas, region, candidate)
            self._sync_recontour(record, canvas, finalized=True)
            return True
        except Exception:
            logger.warning("Pen taper could not finish; preserving the live stroke", exc_info=True)
            self.error = "Pen taper could not be completed. The stroke was preserved."
            self._sync_recontour(record, canvas, finalized=False)
            return False
        finally:
            self.close()
            self.entry_commands.clear()

    def _sync_recontour(self, record: Any, canvas: Any, *, finalized: bool) -> None:
        if record is None or record.closed or record.error is not None:
            return
        if finalized:
            record.commands = [
                (lane, self._final_command(lane, cmd)) for lane, cmd in record.commands
            ]
        record.expected = record._signature(canvas)

    def _finish_entry(self, canvas: Any, record: Any) -> bool:
        if not self.valid(canvas):
            return False
        for lane, original in self.entry_commands.items():
            if self.lanes[lane][1] > 1e-6:
                continue
            cmd = copy.copy(original)
            cmd.points = [replace(point, width_scale=1.0) for point in cmd.points]
            if record is not None:
                record.capture_before(canvas, cmd)
            applied = submit_live_stroke(canvas, cmd)
            if record is not None:
                record.capture_after(canvas, lane, cmd, applied)
        return True

    def _commit_region(self, canvas: Any, region: QRect, candidate: Any) -> None:
        if not self.valid(canvas):
            raise ValueError("Pen taper target changed before commit")
        if not canvas._in_transaction:
            raise ValueError("Pen taper requires the original stroke transaction")
        layer = self.layer
        previous = crop(layer, region, mask=self.mask)
        if previous == candidate:
            return
        if isinstance(candidate, NativeStrokeImage):
            commit_native_image(
                canvas,
                layer,
                region,
                cast(NativeStrokeImage, previous),
                candidate,
                "CMYK Pen Taper",
            )
            return
        metadata = {"command": "pen_taper"}
        if self.mask:
            metadata["mask_edit"] = True
        canvas._mask_edit_flag = self.mask
        try:
            with canvas._mask_edit_swap(layer):
                deferred = canvas._txn_try_defer_paint(
                    layer, region, layer.offset_x, layer.offset_y
                )
                if not deferred:
                    raise ValueError("Pen taper could not join the original transaction")
                paste(
                    layer.image, candidate, region.x() - layer.offset_x, region.y() - layer.offset_y
                )
                layer.texture_version += 1
            if self.mask:
                canvas._gpu_mask_dirty_layers.add(layer.id)
                canvas._schedule_mask_commit()
            canvas._emit_layer_content_changed(layer, region, metadata)
            canvas._recompose_region(region)
        except Exception:
            self._restore_region(canvas, layer, region, previous, metadata)
            raise
        finally:
            canvas._mask_edit_flag = False

    def _restore_region(
        self, canvas: Any, layer: Any, region: QRect, previous: Any, metadata: dict
    ) -> None:
        with canvas._mask_edit_swap(layer):
            paste(
                layer.image,
                previous,
                region.x() - layer.offset_x,
                region.y() - layer.offset_y,
            )
            layer.texture_version += 1
        self.expected = self._signature(canvas)
        if self.mask:
            canvas._gpu_mask_dirty_layers.add(layer.id)
        try:
            canvas._emit_layer_content_changed(layer, region, metadata)
        except Exception:
            logger.warning("Pen taper rollback notification failed", exc_info=True)
        # Use the existing render service directly if the facade notification failed.
        CanvasRenderInvalidationService(canvas).recompose_region(region)
