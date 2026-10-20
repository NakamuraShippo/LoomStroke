"""Prepare a whole-layer filter without mutating the document until commit."""

from __future__ import annotations

import copy
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPainter

from .comic_panels.drawing_state import panel_input_busy
from .filter_pen.command import TILE_SIZE, FilterPenStrokeCommand
from .filter_pen.snapshot import FilterPenSnapshot
from .filter_pen.worker import FilterPenWorker
from .filters import FilterRegistry
from .raster_edit_geometry import raster_ancestors
from .rasterized_filter_command import RasterizedFilterCommand
from .rasterized_filter_target import RasterizedFilterTarget
from .selection_state import SelectionStateService

logger = logging.getLogger(__name__)
_input_busy: Callable[[Any], bool] = panel_input_busy

_TARGET_ERRORS = {
    "Filter Pen requires a raster layer.": "Select a raster layer to apply a filter.",
    "Filter Pen cannot paint a locked layer or a layer mask.": (
        "Filters cannot edit locked layers or layer masks."
    ),
    "Rasterize the layer transform before using Filter Pen.": (
        "Rasterize the layer transform before applying a filter."
    ),
}


class _WholeLayerCoverage:
    def coverage(self, rect: QRect) -> np.ndarray:
        return np.full((rect.height(), rect.width()), 255, dtype=np.uint8)


class DirectFilterSession(QObject):
    """Own one captured target, worker, detached preview and atomic history entry."""

    ready = Signal(QImage)
    failed = Signal(str)

    def __init__(
        self,
        canvas: Any,
        processor_factory: Any,
        parent: QObject | None = None,
        *,
        rasterized_target: RasterizedFilterTarget | None = None,
    ) -> None:
        super().__init__(parent)
        if _input_busy(canvas):
            raise ValueError("Finish the current stroke or text edit first.")
        self.canvas = canvas
        self._rasterized_target = rasterized_target
        try:
            self.snapshot = (
                rasterized_target.snapshot()
                if rasterized_target is not None
                else FilterPenSnapshot.capture(canvas)
            )
        except ValueError as exc:
            raise ValueError(_TARGET_ERRORS.get(str(exc), str(exc))) from exc
        if any(
            node.locked or node.mask_edit_mode
            for node in raster_ancestors(canvas, self.snapshot.layer)
        ):
            raise ValueError("Unlock the layer and its parent groups before applying a filter.")
        mask, _ = SelectionStateService(canvas).selection_clip_state()
        self._selection_key = mask.cacheKey() if mask is not None else None
        self._worker = FilterPenWorker(processor_factory, self)
        self._worker.ready.connect(self._filtered)
        self.destroyed.connect(self._worker.close)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._prepare_tiles)
        self._token = 0
        self._busy = False
        self._queued: tuple[int, str, dict[str, Any]] | None = None
        self._closed = False
        self._command: FilterPenStrokeCommand | None = None
        self._filtered_image: QImage | None = None
        self._keys: deque[tuple[int, int]] = deque()
        self._preview = QImage()
        self._filter_type = ""
        self._filter_name = ""
        self.params: dict[str, Any] = {}

    def valid(self) -> bool:
        return bool(
            not self._closed
            and not _input_busy(self.canvas)
            and (self._rasterized_target is None or self._rasterized_target.valid(self.canvas))
            and self.snapshot.valid(
                self._rasterized_target.view if self._rasterized_target else self.canvas,
                self.snapshot.texture_version,
            )
            and not any(
                node.mask_edit_mode for node in raster_ancestors(self.canvas, self.snapshot.layer)
            )
            and (self.canvas._width, self.canvas._height) == self.snapshot.canvas_size
            and self.canvas._dpi == self.snapshot.document_dpi
            and self._same_selection()
        )

    def _same_selection(self) -> bool:
        mask, bounds = SelectionStateService(self.canvas).selection_clip_state()
        return bool(
            bounds == self.snapshot.selection_bounds
            and (mask.cacheKey() if mask is not None else None) == self._selection_key
        )

    def request(self, filter_type: str, params: dict[str, Any]) -> None:
        if not self.valid():
            self.failed.emit("The editing target changed. Reopen the filter to continue.")
            return
        filter_class = FilterRegistry.get(filter_type)
        if filter_class is None:
            self.failed.emit("The selected filter is not available.")
            return
        self._token += 1
        self._command = None
        self._filtered_image = None
        self._timer.stop()
        self._keys.clear()
        self._filter_type, self.params = filter_type, copy.deepcopy(params)
        self._filter_name = filter_class().name()
        self._queued = (self._token, filter_type, copy.deepcopy(params))
        self._start_worker()

    def _start_worker(self) -> None:
        if self._busy or self._queued is None:
            return
        token, filter_type, params = self._queued
        self._queued = None
        self._busy = True
        self._worker.submit(token, self.snapshot, filter_type, params)

    def _filtered(self, token: int, image: QImage | None, error: Exception | None) -> None:
        self._busy = False
        if self._closed:
            return
        if token != self._token:
            self._start_worker()
            return
        if error is not None or not isinstance(image, QImage) or image.isNull():
            logger.warning("Direct filter evaluation failed: %s", error)
            self.failed.emit("The filter could not be applied. The original pixels were preserved.")
            return
        if image.size() != self.snapshot.image.size() or not self.valid():
            self.failed.emit("The editing target changed. Reopen the filter to continue.")
            return
        self._filtered_image = image
        self._command = (
            RasterizedFilterCommand(self.snapshot, self._rasterized_target)
            if self._rasterized_target
            else FilterPenStrokeCommand(self.snapshot)
        )
        self._command.description = f"Filter: {self._filter_name}"
        self._keys = deque(
            (x, y)
            for y in range((image.height() + TILE_SIZE - 1) // TILE_SIZE)
            for x in range((image.width() + TILE_SIZE - 1) // TILE_SIZE)
        )
        self._preview = self.snapshot.image.scaled(
            400, 300, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self._timer.start(0)

    def _prepare_tiles(self) -> None:
        command, filtered = self._command, self._filtered_image
        if command is None or filtered is None:
            self._timer.stop()
            return
        if not self.valid():
            self._timer.stop()
            self._command = None
            self.failed.emit("The editing target changed. Reopen the filter to continue.")
            return
        start = time.monotonic()
        try:
            while self._keys:
                key = self._keys.popleft()
                rect = QRect(key[0] * TILE_SIZE, key[1] * TILE_SIZE, TILE_SIZE, TILE_SIZE)
                tiles = command.prepare(
                    self.snapshot, filtered, _WholeLayerCoverage(), rect, 1.0, {key}
                )
                command.tiles.update(tiles)
                for tile in tiles.values():
                    image = tile.after
                    if not isinstance(image, QImage):
                        from .channels.channel_pixel_policy import (  # noqa: PLC0415
                            ChannelPixelPolicy,
                        )

                        image = ChannelPixelPolicy.image(image.preview(), straight=True)
                    painter = QPainter(self._preview)
                    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                    painter.scale(
                        self._preview.width() / self.snapshot.image.width(),
                        self._preview.height() / self.snapshot.image.height(),
                    )
                    painter.drawImage(tile.rect, image)
                    painter.end()
                if time.monotonic() - start >= 0.008:
                    return
        except Exception:
            logger.exception("Direct filter tile preparation failed")
            self._timer.stop()
            self._command = None
            self.failed.emit("The filter could not be applied. The original pixels were preserved.")
            return
        self._timer.stop()
        self.ready.emit(self._preview)

    def commit(self) -> bool:
        if self._command is None or self._keys or not self.valid():
            raise ValueError("The editing target changed. Reopen the filter to continue.")
        if not self._command.tiles and self._rasterized_target is None:
            return True
        return bool(self.canvas.submit(self._command))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._token += 1
        self._queued = self._command = self._filtered_image = None
        self._keys.clear()
        self._worker.close()
