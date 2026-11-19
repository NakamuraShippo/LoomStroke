"""Keep live paint-tool pixels off the document until one sparse history commit."""

from __future__ import annotations

import logging
from collections import deque
from math import ceil, isfinite
from time import perf_counter
from weakref import ref

from PySide6.QtCore import QCoreApplication, QRect, Qt, QTimer

from .canvas_command_commit_guard import CanvasCommandCommitGuard
from .comic_panels.panel_gate import panel_stroke_reason
from .command_layer_access import _layer_transform
from .command_types import Command
from .layer_command_commit_context import _LayerCommandContext
from .paint_tool_preview import PaintToolPreview
from .raster_edit_geometry import (
    RasterEditGeometry,
    clip_signature,
    edit_coverage,
    raster_ancestors,
)
from .selection_state import SelectionStateService

logger = logging.getLogger(__name__)

# Seconds of dab painting allowed in each window of pen input. Whatever a fast stroke on a
# large page needs beyond this is painted a moment later, behind the pen.
PAINT_BUDGET = 0.009
PAINT_WINDOW = 0.014


def cancel_pending_paint(canvas, *, commit=False):
    """End an unfinished stroke before another edit; commit it when its tool asked for that."""
    session = getattr(canvas, "_paint_tool_session", None)
    if session is None or session.applying:
        return
    if commit and session.commit_on_interrupt and session.current():
        try:
            if session.finish():
                # The tool still holds this stroke; its later packets are accepted as no-ops.
                canvas._paint_tool_settled = session.stroke_id
                return
        except Exception:
            logger.exception("Could not commit the interrupted paint stroke")
    if getattr(canvas, "_paint_tool_session", None) is session:
        session.close(stale=not session.current())


def _selection_key(canvas):
    selection = SelectionStateService(canvas).selection_clip_state()[0]
    return None if selection is None else selection.cacheKey()


def build_media_stroke(canvas, layer, command):
    """Create the stroke a begin packet describes, limited to the editable area."""
    from .paint_media import create_media_stroke  # noqa: PLC0415

    stroke = create_media_stroke(
        layer,
        command.medium,
        command.diameter,
        color_bgr=command.color_bgr,
        opacity=command.opacity,
        paper=command.paper,
        water_amount=command.water_amount,
        paint_load=command.paint_load,
        tip=command.tip,
        wear=command.wear,
    )
    stroke.set_clip_rect(
        QRect(-int(layer.offset_x), -int(layer.offset_y), *canvas.get_canvas_size())
    )
    if command.surface is not None:
        stroke.set_surface(*command.surface)
    selection = SelectionStateService(canvas).selection_clip_state()[0]
    masked = any(
        node.mask_enabled and node.layer_mask is not None
        for node in raster_ancestors(canvas, layer)
    )
    if selection is not None or masked:
        geometry = RasterEditGeometry.capture(layer)
        stroke.set_coverage(lambda rect: edit_coverage(canvas, layer, geometry, rect, selection))
    return stroke


def _editable(canvas, layer):
    if (
        layer is None
        or layer.layer_type != "raster"
        or layer.image is None
        or layer.image.isNull()
        or layer.native_cmyk is not None
        or canvas._mask_edit_flag
        or layer._preview_transform is not None
        or panel_stroke_reason(canvas, layer)
    ):
        return False
    channels = canvas.channels()
    transform = _layer_transform(layer)
    return (
        channels.selected == (0, 1, 2)
        and not channels.is_alpha(layer)
        and transform.scale_x == transform.scale_y == 1
        and transform.rotation == 0
        and transform.tx == int(layer.offset_x)
        and transform.ty == int(layer.offset_y)
        and all(
            node.visible and not node.locked and not node.mask_edit_mode
            for node in raster_ancestors(canvas, layer)
        )
    )


class PaintToolSession:
    def __init__(self, canvas, layer, command):
        self.canvas, self.layer, self.stroke_id = canvas, layer, command.stroke_id
        self.command = command
        self.commit_on_interrupt = command.commit_on_interrupt
        self.source_version, self.source_key = layer.texture_version, layer.image.cacheKey()
        self.alpha_lock = layer.alpha_lock
        self.origin = layer.offset_x, layer.offset_y
        self.size = canvas.get_canvas_size()
        self.scope = _LayerCommandContext(canvas, (layer,))
        self.selection_key = _selection_key(canvas)
        self.clip = clip_signature(canvas, layer)
        self.stroke = self._create_stroke()
        self.preview = PaintToolPreview(self)
        # Dabs received but not painted yet, and the time already spent in this window.
        self.pending = deque()
        self._window = self._used = 0.0
        self._timer = None
        self.applying = False
        layer._paint_tool_display = self.preview

    def _create_stroke(self):
        return build_media_stroke(self.canvas, self.layer, self.command)

    def current(self):
        if self.applying:
            return True
        layer = self.layer
        return (
            self.canvas._get_active_layer() is layer
            and self.scope.current()
            and self.size == self.canvas.get_canvas_size()
            and _editable(self.canvas, layer)
            and _selection_key(self.canvas) == self.selection_key
            and clip_signature(self.canvas, layer) == self.clip
            and (layer.offset_x, layer.offset_y) == self.origin
            and layer.alpha_lock == self.alpha_lock
            and layer.texture_version == self.source_version
            and layer.image.cacheKey() == self.source_key
        )

    def refresh(self, local_rect):
        if not local_rect.isEmpty():
            self.canvas._recompose_region(local_rect.translated(*self.origin))

    def update(self, points, *, replay=False):
        if replay:
            # The tool resends the whole stroke, e.g. with its final taper. Tiles the first
            # pass touched are kept so that the preview returns them to the source pixels.
            touched = list(self.stroke.tiles)
            self.stroke = self._create_stroke()
            for key in touched:
                self.stroke._tile(*key)
            self.pending.clear()
        self.pending.extend(points)
        self._advance(everything=replay, whole=replay)

    def _advance(self, *, everything=False, whole=False):
        """Paint waiting dabs. Unless everything is asked for, stop when this window's share
        of time is used, so a fast stroke on a large page cannot hold up pen input and
        display; the rest follows from the timer, the next packet, or the end of the stroke.
        """
        from .paint_media.stroke import TILE  # noqa: PLC0415

        start = perf_counter()
        if start - self._window >= PAINT_WINDOW:
            self._window, self._used = start, 0.0
        deadline = None if everything else start + PAINT_BUDGET - self._used
        pending, stroke = self.pending, self.stroke
        origins = set()
        ox, oy = self.origin
        while pending and (deadline is None or perf_counter() < deadline):
            x, y, *contact = pending.popleft()
            # Eleven values end with the dab's own BGR colour.
            color = contact[6:] or None
            origins.update(stroke.dab(round(x) - ox, round(y) - oy, *contact[:6], color_bgr=color))
        self._used += perf_counter() - start
        tiles = (
            list(stroke.tiles.values())
            if whole
            else [stroke.tiles[x // TILE, y // TILE] for x, y in origins]
        )
        self.preview.update(self, tiles)
        if pending:
            self._resume_later()
        elif self._timer is not None:
            self._timer.stop()

    def _resume_later(self):
        if QCoreApplication.instance() is None:
            return  # No event loop: the next packet or the end of the stroke paints the rest.
        if self._timer is None:
            self._timer = QTimer()
            self._timer.setSingleShot(True)
            self._timer.setTimerType(Qt.TimerType.PreciseTimer)
            self._timer.timeout.connect(self._resume)
        wait = self._window + PAINT_WINDOW - perf_counter()
        self._timer.start(max(0, ceil(wait * 1000)))

    def _resume(self):
        if getattr(self.canvas, "_paint_tool_session", None) is not self or self.applying:
            return
        if not self.current():
            self.close(stale=True)
            return
        try:
            self._advance()
        except Exception:
            logger.exception("Could not continue the paint stroke")
            self.close(stale=not self.current())

    def close(self, *, committed=False, stale=False, notify=True):
        if getattr(self.canvas, "_paint_tool_session", None) is not self:
            return
        self.canvas._paint_tool_session = None
        self.pending.clear()
        if self._timer is not None:
            self._timer.stop()
            self._timer.timeout.disconnect(self._resume)
            self._timer = None
        self.preview.finish(self, committed=committed, stale=stale)
        if notify and self.scope.current():
            self.refresh(self.preview.dirty)

    def _owns_written(self, cmd):
        # Switching the active layer within this same document is allowed after
        # the pixel write: its command still belongs to this document's undo stack.
        return self.scope.current() and self.layer.texture_version == cmd.version

    def finish(self):
        if self.pending:
            self._advance(everything=True)
        cmd = self.stroke.prepare_command()
        if cmd is None:
            self.close()
            return True
        guard = CanvasCommandCommitGuard(self.canvas)
        self.applying = True
        try:
            try:
                applied = cmd.apply(self.canvas)
            except Exception:
                # A notification can raise after QPainter has written the tiles, but
                # before RasterStrokeCommand marks itself executed and supports revert.
                if self._owns_written(cmd) and self.layer.image.cacheKey() != self.source_key:
                    cmd._write(self.canvas, cmd.tiles, before=True, dirty=cmd.local_dirty)
                raise
            if not applied or not self._owns_written(cmd):
                return False
            try:
                self.canvas._record_history_command("Paint", cmd)
                if not self._owns_written(cmd):
                    return False
                brush = self.stroke.brush
                # What an outline edit needs to repaint this stroke from its starting point.
                self.canvas._paint_tool_last_commit = (
                    self.stroke_id,
                    ref(self.layer),  # Never keeps a removed layer and its preview alive.
                    cmd,
                    getattr(brush, "previous", None) if self.stroke.medium == "water" else None,
                )
                self.stroke.commit_medium()
            except Exception:
                if self._owns_written(cmd):
                    guard.rollback(cmd)
                raise
            self.close(committed=True)
            return True
        finally:
            self.applying = False
            self.close(stale=True)


class PaintToolCommand(Command):
    """An input packet; only the resulting RasterStrokeCommand enters history."""

    def __init__(
        self,
        phase,
        stroke_id,
        points=(),
        medium="water",
        diameter=32,
        color_bgr=(0, 0, 0),
        opacity=1.0,
        *,
        paper="Standard",
        water_amount=0.5,
        paint_load=0.5,
        commit_on_interrupt=False,
        replay=False,
        tip=None,
        surface=None,
        wear=None,
    ):
        super().__init__()
        # Watercolor (paint reach, paint left at the end, lifting), each in 0..1.
        self.wear = wear
        # (tip image path or "", edge hardness 0..1, hardness mode) from the basic pen.
        self.tip = tip
        # (paper texture strength, grain size, seed, mixing with the paint below).
        self.surface = surface
        self.commit_on_interrupt, self.replay = commit_on_interrupt, replay
        self.phase, self.stroke_id, self.points = phase, stroke_id, tuple(points)
        self.medium, self.diameter, self.color_bgr = medium, diameter, color_bgr
        self.opacity = opacity
        self.paper, self.water_amount, self.paint_load = paper, water_amount, paint_load

    def _start(self, canvas, session):
        if session is not None:
            session.close(stale=not session.current())
        layer = canvas._get_active_layer()
        if self.stroke_id is None or not 0 <= self.opacity <= 1 or not _editable(canvas, layer):
            return None
        try:
            session = PaintToolSession(canvas, layer, self)
        except (ImportError, OSError, ValueError) as error:
            logger.warning("Could not start paint stroke: %s", error)
            return None
        canvas._paint_tool_session = session
        return session

    def apply(self, canvas):
        session = getattr(canvas, "_paint_tool_session", None)
        if self.phase == "begin":
            canvas._paint_tool_settled = None
            canvas._paint_tool_last_commit = None
            session = self._start(canvas, session)
        elif session is None and self.stroke_id is getattr(canvas, "_paint_tool_settled", None):
            return self.stroke_id is not None
        if session is None or session.stroke_id is not self.stroke_id:
            return False
        if self.phase == "cancel":
            session.close(stale=not session.current())
            return True
        if self.phase not in ("begin", "move", "end") or not session.current():
            session.close(stale=True)
            return False
        try:
            if not all(len(p) in (2, 5, 6, 8, 11) and all(map(isfinite, p)) for p in self.points):
                session.close()
                return False
            if self.points or self.replay:
                session.update(self.points, replay=self.replay)
            return session.finish() if self.phase == "end" else True
        except Exception:
            session.close(stale=not session.current())
            raise

    def revert(self, canvas):
        return False
