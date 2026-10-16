"""Vector pointer geometry in Qt widget pixels, matching the selection overlay."""

from __future__ import annotations

import logging
import math
from typing import Any

from PySide6.QtCore import QPointF
from PySide6.QtGui import QTransform

from ..sdk.handles import handle_positions_for_bounds, hit_test_handle_points

logger = logging.getLogger(__name__)


class VectorHitTestGeometry:
    """Resolve viewport geometry through HostAPI without changing document coordinates."""

    POINT_RADIUS = 3.0
    SHAPE_RADIUS = 5.0

    def __init__(self, host: Any) -> None:
        self.transform = QTransform()
        ui = getattr(host, "ui", None)
        main_window = getattr(ui, "get_main_window", None)
        if callable(main_window):
            try:
                widget = getattr(main_window(), "canvas", None)
                getter = getattr(getattr(widget, "viewport", None), "get_transform", None)
                transform = getter() if callable(getter) else None
                if (
                    isinstance(transform, QTransform)
                    and transform.isInvertible()
                    and all(
                        math.isfinite(value)
                        for value in (
                            transform.m11(),
                            transform.m12(),
                            transform.m21(),
                            transform.m22(),
                            transform.dx(),
                            transform.dy(),
                        )
                    )
                ):
                    self.transform = transform
            except (RuntimeError, AttributeError):
                logger.debug("Vector hit-test viewport is unavailable", exc_info=True)

    def canvas_radius(self, pixels: float = POINT_RADIUS) -> float:
        """Convert logical widget pixels for the viewport's uniform zoom/rotation/flip.

        Qt pointer positions and overlay markers both use logical pixels. Applying
        devicePixelRatio here would count display scaling twice.
        """
        scale = math.hypot(self.transform.m11(), self.transform.m12())
        return pixels / scale if math.isfinite(scale) and scale > 0 else pixels

    def transform_handle(
        self, x: float, y: float, bounds: tuple[float, float, float, float]
    ) -> tuple[str, str] | None:
        """Hit only the visible handles plus a one-pixel margin, then the move area."""
        point = self.transform.map(QPointF(x, y))
        handles = {
            name: self.transform.map(QPointF(*position))
            for name, position in handle_positions_for_bounds(bounds, include_rotate=False).items()
        }
        top = handles["edge-top"]
        center = (handles["corner-tl"] + handles["corner-br"]) * 0.5
        direction = top - center
        length = math.hypot(direction.x(), direction.y())
        direction = direction / length if length > 1e-6 else QPointF(0, -1)
        handles["rotate"] = top + direction * 12.0
        min_x, min_y, max_x, max_y = bounds
        return hit_test_handle_points(
            point.x(),
            point.y(),
            {name: (p.x(), p.y()) for name, p in handles.items()},
            corner_half_extent=5.0,
            edge_radius=4.5,
            rotation_radius=4.5,
            include_move_area=True,
            contains_move_area=lambda _x, _y: min_x <= x <= max_x and min_y <= y <= max_y,
        )
