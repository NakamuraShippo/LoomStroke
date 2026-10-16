"""Input state machine for ellipse, lasso, and click-defined polygon selections."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtGui import QPainterPath

from loomstroke.core.commands import SelectionOp
from loomstroke.core.selection_shapes.selection_shape_mask import SelectionShapeMask
from loomstroke.core.selection_shapes.selection_shape_path import SelectionShapePath
from loomstroke.core.selection_shapes.shape_selection_mask_command import ShapeSelectionMaskCommand

from .free_selection_overlay import FreeSelectionOverlay
from .free_selection_session import FreeSelectionSession
from .selection_preparation_controller import SelectionPreparationController


class FreeSelectionController:
    MODES = ("Ellipse", "Lasso", "Polygon")
    MAX_POINTS = 8192

    def __init__(self, tool: Any) -> None:
        self._tool = tool
        self._session: FreeSelectionSession | None = None
        self._delegating = False
        self._overlay = FreeSelectionOverlay()
        self._preparation = SelectionPreparationController(tool)

    @property
    def enabled(self) -> bool:
        return self._tool.settings.selection_mode in self.MODES

    def reset(self) -> None:
        self._preparation.cancel()
        if self._session is not None and self._tool.host_api is not None:
            self._tool.host_api.input.release_capture(self._tool.descriptor().id)
        self._session = None
        self._delegating = False
        self._overlay.clear()
        overlay = getattr(self._tool, "_selection_overlay", None)
        if overlay is not None:
            overlay.refresh()

    def down(self, point: Any, **modifiers: bool) -> None:
        if self._preparation.busy:
            self._preparation.cancel()
            return
        tool = self._tool
        position = self._position(point)
        if position is None:
            return
        if self._session is None:
            operation = tool._op_from_modifiers(modifiers)
            if tool._transform_active or (
                operation == SelectionOp.REPLACE
                and tool._selection_exists()
                and (
                    not tool._is_outside_selection(point)
                    or tool._hit_test_transform_handle(point) is not None
                )
            ):
                self._delegating = True
                tool._raster_controller.on_pointer_down(point, **modifiers)
                return
            self._session = FreeSelectionSession(tool.settings.selection_mode, operation)
        session = self._session
        if (
            session.mode == "Polygon"
            and len(session.points) >= 3
            and math.dist(position, session.points[0]) <= 3.0
        ):
            self._commit()
            return
        if len(session.points) < self.MAX_POINTS:
            session.points.append(position)
        session.hover = position
        session.square = modifiers.get("shift", False)
        session.center = modifiers.get("alt", False)
        tool.host_api.input.pointer_capture(tool.descriptor().id)
        self._preview()

    def move(self, points: list[Any]) -> None:
        if self._delegating:
            self._tool._raster_controller.on_pointer_move(points)
            return
        session = self._session
        if session is None or not points:
            return
        if session.mode == "Lasso":
            for point in points:
                position = self._position(point)
                if position is None:
                    continue
                if not session.points or math.dist(position, session.points[-1]) >= 0.5:
                    if len(session.points) < self.MAX_POINTS:
                        session.points.append(position)
                    else:
                        # Reduce sampling density while retaining the entire contour.
                        session.points = session.points[::2]
                        session.points.append(position)
        session.hover = self._position(points[-1])
        self._preview()

    def up(self, point: Any) -> None:
        if self._delegating:
            self._tool._raster_controller.on_pointer_up(point)
            self._delegating = False
            return
        if self._session is None:
            return
        self.move([point])
        if self._session.mode == "Polygon":
            self._tool.host_api.input.release_capture(self._tool.descriptor().id)
        else:
            self._commit()

    def key(self, key: str, pressed: bool) -> bool:
        if pressed and key.lower() in {"escape", "esc"} and self._preparation.busy:
            self.reset()
            return True
        session = self._session
        if session is None:
            return False
        lower = key.lower()
        if lower in {"shift", "alt"}:
            if lower == "shift":
                session.square = pressed
            else:
                session.center = pressed
            self._preview()
            return True
        if not pressed:
            return False
        if lower in {"escape", "esc"}:
            self.reset()
            return True
        return session.mode == "Polygon" and self._polygon_key(lower)

    def _polygon_key(self, lower: str) -> bool:
        if lower in {"enter", "return"}:
            self._commit()
            return True
        if lower in {"backspace", "delete"}:
            session = self._session
            if session.points:
                session.points.pop()
            if not session.points:
                self.reset()
            else:
                self._preview()
            return True
        return False

    def _path(self, *, preview: bool = False) -> QPainterPath:
        session = self._session
        if session is None:
            return QPainterPath()
        points = list(session.points)
        if session.mode == "Ellipse" and session.hover is not None:
            points = [points[0], session.hover]
        elif preview and session.mode == "Polygon" and session.hover is not None:
            points.append(session.hover)
        path = SelectionShapePath.build(
            session.mode,
            points,
            square=session.square,
            center=session.center,
            pixel_perfect=self._tool.settings.pixel_perfect,
        )
        if preview and path.isEmpty() and points:
            path.moveTo(*points[0])
            for point in points[1:]:
                path.lineTo(*point)
        return path

    def _preview(self) -> None:
        self._overlay.show(self._tool.host_api, self._path(preview=True))

    def _commit(self) -> None:
        session = self._session
        if session is None:
            return
        settings = self._tool.settings
        path, size = self._path(), self._tool._canvas_size()
        radius = settings.feather_radius if settings.feather_enabled else 0
        bounds = path.boundingRect()
        if radius and min(bounds.width(), size[0]) * min(bounds.height(), size[1]) > 512_000:
            self.reset()
            self._preparation.prepare(path, size, radius, settings.pixel_perfect, session.operation)
            return
        result = SelectionShapeMask.render(
            path,
            size,
            feather_radius=radius,
            pixel_perfect=settings.pixel_perfect,
        )
        if result is not None:
            mask, bounds = result
            self._tool._pending_commands.append(
                ShapeSelectionMaskCommand(mask, session.operation, bounds=bounds)
            )
        self.reset()

    @staticmethod
    def _position(point: Any) -> tuple[float, float] | None:
        x, y = map(float, point.pos)
        return (x, y) if math.isfinite(x) and math.isfinite(y) else None
