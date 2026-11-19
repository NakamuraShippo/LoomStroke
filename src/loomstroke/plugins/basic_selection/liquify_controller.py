"""Stateful raster liquify brush with worker-owned pixel updates."""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Slot

from loomstroke.core.commands import (
    UpdateSelectionMaskCommand,
    _is_qimage_layer,
    _resolve_target_layer,
)
from loomstroke.core.selection_clipboard import SelectionPayload
from loomstroke.core.selection_liquify import (
    MODES,
    Point,
    Segment,
    apply_liquify_segments,
    pad_liquify_region,
)
from loomstroke.core.selection_projective import _image_array, _mask_array

from ..common.page_context import page_context
from .liquify_layer_preview import LiquifyLayerFrame, LiquifyLayerPreview
from .projective_preview import _apply_preview, update_preview_mask
from .selection_worker_signals import SelectionWorkerSignals

if TYPE_CHECKING:
    from .rect_tool import BasicSelectionRectTool

logger = logging.getLogger(__name__)
Batch = tuple[tuple[Segment, ...], str, int, float]


class _LiquifyWorker(QRunnable):
    def __init__(
        self,
        image: np.ndarray,
        mask: np.ndarray,
        bounds: tuple[int, int, int, int],
        batch: Batch,
        binary_mask: bool,
        base: LiquifyLayerFrame | None,
        payload: SelectionPayload,
    ) -> None:
        super().__init__()
        self.image, self.mask, self.bounds = image, mask, bounds
        self.segments, self.mode, self.radius, self.strength = batch
        self.binary_mask = binary_mask
        self.base, self.payload = base, payload
        self.frame: LiquifyLayerFrame | None = None
        self.signals = SelectionWorkerSignals()

    def run(self) -> None:
        try:
            changed = apply_liquify_segments(
                self.image,
                self.mask,
                self.bounds,
                self.segments,
                mode=self.mode,
                radius=self.radius,
                strength=self.strength,
                binary_mask=self.binary_mask,
            )
            if self.base is not None:
                self.frame = self.base.compose(
                    self.payload, self.image, self.mask, self.bounds, binary=self.binary_mask
                )
            self.signals.finished.emit(changed, None)
        except Exception as error:
            self.signals.finished.emit(None, str(error))


class LiquifyController(QObject):
    def __init__(self, tool: BasicSelectionRectTool) -> None:
        super().__init__()
        self._tool = tool
        self.active = False
        self.mode = "push"
        self.cursor: Point | None = None
        self._drag_previous: Point | None = None
        self._image: np.ndarray | None = None
        self._mask: np.ndarray | None = None
        self._original_image: np.ndarray | None = None
        self._original_mask: np.ndarray | None = None
        self._bounds: tuple[int, int, int, int] | None = None
        self._premultiplied = True
        self._generation = 0
        self._running: tuple[int, tuple[Any, ...]] | None = None
        self._worker: _LiquifyWorker | None = None
        self._display: LiquifyLayerPreview | None = None
        self._display_canvas: Any | None = None
        self._pending: deque[Batch] = deque()
        self._commit_requested = False
        self._reset_pending = False
        self.changed = False

    @property
    def busy(self) -> bool:
        return self._running is not None or bool(self._pending)

    def start(self, mode: str) -> bool:  # noqa: PLR0911
        tool = self._tool
        if mode not in MODES:
            return False
        if self.active:
            self.set_mode(mode)
            return True
        if tool._transform_active:
            self._notify("Apply the current transform before changing mode.")
            return False
        host = tool.host_api
        canvas = getattr(host, "canvas", None) if host is not None else None
        layer = _resolve_target_layer(canvas, None) if canvas is not None else None
        if (
            tool._is_vector_mode()
            or layer is None
            or getattr(layer, "native_cmyk", None) is not None
        ):
            self._notify("This transform requires an RGB raster selection.")
            return False
        original = LiquifyLayerFrame.capture(layer) if _is_qimage_layer(layer) else None
        if not tool._ensure_transform_session(preview=False, fast_cut=True):
            return False
        payload = tool._transform_payload
        if payload is None or not self.begin(payload, mode, original):
            tool._cancel_transform_session()
            self._notify("Unable to apply selection.")
            return False
        tool._transform_mode_override = "liquify"
        tool._selection_overlay.refresh()
        return True

    def begin(
        self, payload: SelectionPayload, mode: str, original: LiquifyLayerFrame | None
    ) -> bool:
        host = self._tool.host_api
        if host is None or mode not in MODES:
            return False
        layer = _resolve_target_layer(host.canvas, None)
        premultiplied = _is_qimage_layer(layer)
        image = _image_array(payload, premultiplied=premultiplied)
        mask = _mask_array(payload)
        if image is None or mask is None or image.shape[:2] != mask.shape:
            return False
        canvas_size = UpdateSelectionMaskCommand._canvas_size(host.canvas)
        image, mask, bounds = pad_liquify_region(image, mask, payload.bounds, canvas_size, 512)
        self._generation += 1
        self._image, self._mask = image, mask
        self._original_image, self._original_mask = image, mask
        self._bounds = bounds
        self._premultiplied = premultiplied
        self._pending.clear()
        self._commit_requested = self._reset_pending = False
        self.changed = False
        self._drag_previous = None
        self.cursor = None
        self.active = True
        self.mode = mode
        if original is not None:
            self._display = LiquifyLayerPreview(
                original, host.canvas, bounds, self._tool._layer_transform.active
            )
            self._display_canvas = host.canvas
            host.canvas._liquify_layer_preview = self._display
        self._publish(keep_selection=self._tool._layer_transform.active)
        return True

    def set_mode(self, mode: str) -> None:
        if mode in MODES:
            self.mode = mode

    def cancel(self) -> None:
        if self._drag_previous is not None and self._tool.host_api is not None:
            self._tool.host_api.input.release_capture(self._tool.descriptor().id)
        self._generation += 1
        if self._display is not None:
            canvas = self._display_canvas
            if getattr(canvas, "_liquify_layer_preview", None) is self._display:
                canvas._liquify_layer_preview = None
                if canvas._selection_preview_image is self._display.current.image:
                    canvas._selection_preview_image = None
            self._display = self._display_canvas = None
        self.active = False
        self.cursor = self._drag_previous = None
        self._image = self._mask = None
        self._original_image = self._original_mask = None
        self._bounds = None
        self._pending.clear()
        self._commit_requested = self._reset_pending = False
        self.changed = False

    def reset(self) -> None:
        if not self.active:
            return
        self._generation += 1
        self._pending.clear()
        self._commit_requested = False
        self._reset_pending = True
        if self._drag_previous is not None and self._tool.host_api is not None:
            self._tool.host_api.input.release_capture(self._tool.descriptor().id)
        self._drag_previous = None
        if self._running is None:
            self._finish_reset()

    def result(self) -> tuple[np.ndarray, np.ndarray, tuple[int, int, int, int]] | None:
        if not self.active or self.busy or self._reset_pending:
            return None
        if self._image is None or self._mask is None or self._bounds is None:
            return None
        return self._image, self._mask, self._bounds

    def commit_when_ready(self) -> None:
        self._commit_requested = True
        if not self.busy and not self._reset_pending:
            self._submit()

    def pointer_down(self, point: Point) -> None:
        if not self.active or self._bounds is None or self._reset_pending:
            return
        self.cursor = point
        x, y, width, height = self._bounds
        if not (x <= point[0] < x + width and y <= point[1] < y + height):
            self._drag_previous = None
            return
        self._drag_previous = point
        host = self._tool.host_api
        if host is not None:
            host.input.pointer_capture(self._tool.descriptor().id)
        if self.mode not in {"push", "push_left"}:
            self._enqueue(((point, point),))

    def pointer_move(self, points: list[Point]) -> None:
        if not self.active or not points or self._reset_pending:
            return
        self.cursor = points[-1]
        if self._drag_previous is None:
            self._tool._selection_overlay.refresh()
            return
        segments: list[Segment] = []
        previous = self._drag_previous
        for point in points:
            if point != previous:
                segments.append((previous, point))
            previous = point
        self._drag_previous = previous
        self._enqueue(tuple(segments))
        self._tool._selection_overlay.refresh()

    def pointer_up(self, point: Point) -> None:
        if self._drag_previous is not None and point != self._drag_previous:
            self._enqueue(((self._drag_previous, point),))
        self.cursor = point
        self._drag_previous = None
        host = self._tool.host_api
        if host is not None:
            host.input.release_capture(self._tool.descriptor().id)
        self._tool._selection_overlay.refresh()

    def _enqueue(self, segments: tuple[Segment, ...]) -> None:
        if not segments or not self.active or self._reset_pending:
            return
        settings = self._tool.settings
        radius = max(1, min(512, int(settings.liquify_radius)))
        strength = max(0.01, min(1.0, float(settings.liquify_strength) / 100.0))
        self._pending.append((segments, self.mode, radius, strength))
        if self._running is None:
            self._start_next()

    def _context(self) -> tuple[Any, ...] | None:
        host = self._tool.host_api
        payload = self._tool._transform_payload
        if host is None or payload is None:
            return None
        layer = _resolve_target_layer(host.canvas, None)
        return (id(host.canvas), page_context(host), id(payload), getattr(layer, "id", None))

    def _start_next(self) -> None:
        if not self.active or not self._pending or self._running is not None:
            return
        context = self._context()
        payload = self._tool._transform_payload
        if (
            context is None
            or payload is None
            or self._image is None
            or self._mask is None
            or self._bounds is None
        ):
            return
        if self._image is self._original_image:
            self._image, self._mask = self._image.copy(), self._mask.copy()
        batch = self._pending.popleft()
        worker = _LiquifyWorker(
            self._image,
            self._mask,
            self._bounds,
            batch,
            self._tool._transform_mask_mode == "binary",
            self._display.base if self._display is not None else None,
            payload,
        )
        worker.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._worker = worker
        self._running = (self._generation, context)
        QThreadPool.globalInstance().start(worker)

    @Slot(object, object)
    def _finished(self, changed: Any, error: Any) -> None:  # noqa: PLR0911
        running, worker = self._running, self._worker
        self._running, self._worker = None, None
        if running is None or worker is None:
            return
        generation, context = running
        if generation != self._generation:
            if self._reset_pending:
                self._finish_reset()
            elif self._pending:
                self._start_next()
            elif self.active and self._commit_requested:
                self._submit()
            return
        if context != self._context():
            host = self._tool.host_api
            if host is not None and (id(host.canvas), page_context(host)) == context[:2]:
                self._tool._cancel_transform_session()
            else:
                self.cancel()
            return
        if error is not None or changed is None:
            logger.error("Liquify stroke failed: %s", error)
            self._tool._cancel_transform_session()
            self._notify("Unable to apply selection.")
            return
        self.changed |= bool(changed)
        if self._pending:
            self._start_next()
            return
        if self._commit_requested:
            self._submit()
            return
        self._publish(worker.frame)

    def _finish_reset(self) -> None:
        self._reset_pending = False
        if not self.active or self._original_image is None or self._original_mask is None:
            return
        self._image, self._mask = self._original_image.copy(), self._original_mask.copy()
        self.changed = False
        self._publish()
        if self._commit_requested:
            self._submit()

    def _publish(
        self, frame: LiquifyLayerFrame | None = None, *, keep_selection: bool = False
    ) -> None:
        host = self._tool.host_api
        if host is None or not self.active or self._image is None or self._mask is None:
            return
        canvas = host.canvas
        width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
        if self._bounds is None:
            return
        if self._display is not None:
            self._display.current = frame or self._display.original
            canvas._selection_preview_image = self._display.current.image
            # Layer-owned pixels are already in the scene; the floating overlay needs no bounds.
            canvas._selection_preview_bounds = None
            if not keep_selection:
                update_preview_mask(canvas, self._mask, self._bounds, width, height)
            self._tool._recompose_canvas()
        elif _apply_preview(
            canvas,
            (self._image, self._mask, self._bounds),
            width,
            height,
            premultiplied=self._premultiplied,
        ):
            self._tool._recompose_canvas()

    def _submit(self) -> None:
        self._commit_requested = False
        command = self._tool._build_transform_commit_command()
        if command is None:
            return
        self._tool._submit_ready_transform_command(command)

    def _notify(self, source: str) -> None:
        from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

        message = translated_literal_domain("BasicSelectionRectTool", source, use_fallback=True)
        self._tool._notify_message(message)
