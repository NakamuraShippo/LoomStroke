"""Editable path of one committed watercolor or oil stroke.

A medium stroke has no outline to redraw; moving an anchor repaints the whole stroke along
the edited path, from the pixels and wet state that preceded it, as one history entry.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import Future
from typing import Any

from PySide6.QtCore import QRect

from ..paint_media.watercolor import WetLayer
from ..paint_tool_session import build_media_stroke
from ..raster_stroke_command import RasterStrokeCommand, _StrokeTile
from .geometry import Anchor, Curve
from .session import _FITTER, _fit_curves

logger = logging.getLogger(__name__)
_FAILED = "Recontour could not edit this stroke. The raster stroke was preserved."


def _lane(dab: tuple) -> int:
    return int(dab[5]) if len(dab) > 5 else 0


class MediaStrokeRecord:
    """Same surface as RasterStrokeSession for the pen's outline controller."""

    def __init__(
        self,
        stroke_id: object,
        begin: Any,
        dabs: tuple,
        paths: tuple,
        step: float,
        smoothness: float,
    ) -> None:
        self.stroke_id, self.begin, self.dabs = stroke_id, begin, tuple(dabs)
        # Pen position of each dab before scatter; the handles follow this path.
        self.paths = tuple(paths)
        if len(self.paths) != len(self.dabs):
            raise ValueError("Each dab needs its path position")
        # Distance between dabs as the pen spaced them.
        self.step = max(1.0, step)
        self.smoothness = smoothness
        self.curves: dict[int, Curve] = {}
        self.edited: dict[int, tuple[Anchor, ...]] = {}
        # Tile key -> (rect, pixels before the stroke, pixels now).
        self.tiles: dict[tuple[int, int], tuple] = {}
        self.wet: Any = None
        self.layer: Any = None
        self.canvas: Any = None
        self.expected: tuple = ()
        self.error: str | None = None
        self.closed = False
        self.ready = False
        self.future: Future | None = None

    def close(self) -> None:
        self.closed = True
        self.tiles.clear()
        self.dabs = self.paths = ()
        if self.future is not None:
            self.future.cancel()
            self.future = None

    def fail(self) -> None:
        logger.exception("Recontour failed; paint stroke preserved")
        self.error = _FAILED
        self.close()

    def _signature(self, canvas: Any) -> tuple:
        layer = canvas._get_active_layer()
        if layer is None or layer.image is None:
            return ()
        selection = getattr(canvas, "_selection_mask", None)
        return (
            id(canvas),
            id(layer),
            layer.image.cacheKey(),
            layer.texture_version,
            bool(layer.alpha_lock),
            bool(layer.locked),
            layer.offset_x,
            layer.offset_y,
            repr(layer.transform),
            selection.cacheKey() if selection is not None else None,
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

    def seal(self, canvas: Any) -> bool:
        commit = getattr(canvas, "_paint_tool_last_commit", None)
        if self.closed or commit is None or commit[0] is not self.stroke_id or not self.dabs:
            self.close()
            return False
        _, layer_ref, command, wet = commit
        layer = layer_ref()
        if (
            layer is None
            or canvas._get_active_layer() is not layer
            or layer.texture_version != command.version
        ):
            self.close()
            return False
        self.canvas, self.layer, self.wet = canvas, layer, wet
        self.tiles = {
            key: (QRect(tile.rect), tile.before, tile.after) for key, tile in command.tiles.items()
        }
        self.expected = self._signature(canvas)
        positions: dict[int, list] = {}
        for dab, path in zip(self.dabs, self.paths, strict=True):
            positions.setdefault(_lane(dab), []).append((float(path[0]), float(path[1])))
        if len(self.dabs) > 1024:
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

    def _project(self, edited: dict[int, tuple[Anchor, ...]]) -> list[tuple]:
        positions = {lane: curve.project(edited[lane]) for lane, curve in self.curves.items()}
        cursors = dict.fromkeys(positions, 0)
        last: dict[int, tuple[int, int]] = {}
        previous: dict[int, tuple] = {}
        moved = []
        for dab, path in zip(self.dabs, self.paths, strict=True):
            lane = _lane(dab)
            x, y = positions[lane][cursors[lane]]
            cursors[lane] += 1
            # A scattered dab keeps its offset from the pen path.
            here = (x, y, path, dab[0] - path[0], dab[1] - path[1], dab)
            before = previous.get(lane)
            previous[lane] = here
            fill = () if before is None else self._fill(before, here)
            for px, py, values in (*fill, (x + here[3], y + here[4], dab[2:])):
                position = round(px), round(py)
                if last.get(lane) != position:  # Squeezed dabs must not pile up on one pixel.
                    last[lane] = position
                    moved.append((*position, *values))
        return moved

    def _fill(self, before: tuple, here: tuple) -> tuple:
        """Extra dabs for a stretched gap, so the stroke stays as dense as it was drawn."""
        bx, by, earlier_path, bdx, bdy, earlier = before
        x, y, path, dx, dy, dab = here
        drawn = math.hypot(path[0] - earlier_path[0], path[1] - earlier_path[1])
        count = math.ceil(math.hypot(x - bx, y - by) / max(self.step, drawn)) - 1
        extra = []
        for i in range(1, min(count, 4096) + 1):
            t = i / (count + 1)
            values = list(dab[2:])
            values[0] = earlier[2] + (dab[2] - earlier[2]) * t
            if len(dab) > 6 and len(earlier) > 6:
                values[4] = earlier[6] + (dab[6] - earlier[6]) * t
            extra.append(
                (
                    bx + (x - bx) * t + bdx + (dx - bdx) * t,
                    by + (y - by) * t + bdy + (dy - bdy) * t,
                    tuple(values),
                )
            )
        return tuple(extra)

    def commit(
        self, canvas: Any, edited: dict[int, tuple[Anchor, ...]], *, cancel: bool = False
    ) -> bool:
        if not self.ready or not self.valid(canvas):
            return False
        if not cancel and edited == self.edited:
            return True
        base = {lane: curve.anchors for lane, curve in self.curves.items()}
        if cancel:
            edited = base
        dabs = list(self.dabs) if edited == base else self._project(edited)
        return self._repaint(canvas, dabs, edited)

    def _repaint(self, canvas: Any, dabs: list[tuple], edited: dict) -> bool:
        layer = self.layer
        area = QRect()
        restore = RasterStrokeCommand(layer)
        for key, (rect, source, current) in self.tiles.items():
            restore.tiles[key] = _StrokeTile(rect, current, source)
            area = area.united(rect)
        restore.local_dirty = QRect(area)
        if not restore.apply(canvas, refresh=False):
            return False
        try:
            wet = self.wet
            layer._paint_medium_wet_state = (
                None
                if wet is None
                else WetLayer(layer.texture_version, layer.image.cacheKey(), wet.turn, wet.tiles)
            )
            stroke = build_media_stroke(canvas, layer, self.begin)
            ox, oy = int(layer.offset_x), int(layer.offset_y)
            for x, y, *contact in dabs:
                stroke.dab(x - ox, y - oy, *contact[:6], color_bgr=contact[6:] or None)
            painted = stroke.prepare_command()
            fresh = painted.tiles if painted is not None else {}
            final, visible = RasterStrokeCommand(layer), RasterStrokeCommand(layer)
            tiles = {}
            for key in {*self.tiles, *fresh}:
                if key in self.tiles:
                    rect, source, current = self.tiles[key]
                else:
                    rect, source = fresh[key].rect, fresh[key].before
                    current = source
                after = fresh[key].after if key in fresh else source
                tiles[key] = (QRect(rect), source, after)
                visible.tiles[key] = _StrokeTile(rect, current, after)
                if after != current:
                    final.tiles[key] = _StrokeTile(rect, current, after)
                    area = area.united(rect)
            # The layer holds the pre-stroke pixels now; show the repainted stroke, and
            # keep only the tiles that really changed for the history entry.
            visible.local_dirty = QRect(area)
            if not visible.apply(canvas):
                raise RuntimeError("Target layer changed during Recontour")
            if final.tiles:
                final.local_dirty = QRect(area)
                final._executed = True
                final.version = layer.texture_version
                final._set_dirty()
                canvas._record_history_command("Recontour", final)
            stroke.commit_medium()
        except Exception:
            # Put back exactly what was on the layer before this adjustment.
            restore._write(canvas, restore.tiles, before=True, dirty=restore.local_dirty)
            raise
        self.tiles = tiles
        self.edited = dict(edited)
        self.expected = self._signature(canvas)
        return True
