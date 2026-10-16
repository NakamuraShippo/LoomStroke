"""Viewport management for canvas zoom, pan, and rotation."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Final

from PySide6.QtCore import QObject, QPointF, QRectF, Signal
from PySide6.QtGui import QTransform

logger = logging.getLogger(__name__)

# Epsilon for preventing zero/near-zero zoom values
EPS: Final[float] = 1e-4


@dataclass
class ViewportState:
    """Viewport state data."""

    # Position (canvas coordinates of widget center)
    # Initialize to center of default widget size
    center_x: float = 400.0  # widget_width / 2
    center_y: float = 300.0  # widget_height / 2

    # Zoom (ensure never goes below EPS)
    zoom: float = 1.0
    min_zoom: float = 0.01  # Will be enforced with EPS
    max_zoom: float = 32.0

    # Rotation (degrees)
    rotation: float = 0.0

    # Widget dimensions
    widget_width: int = 800
    widget_height: int = 600

    # Constraints
    allow_rotation: bool = True
    constrain_to_canvas: bool = False
    smooth_zoom: bool = True


class Viewport(QObject):
    """Manages canvas viewport transformations.

    Handles zoom, pan, rotation and coordinate transformations
    between canvas space and widget space.
    """

    # Signals
    viewport_changed = Signal(dict)  # Emitted when viewport changes
    zoom_changed = Signal(float)  # Emitted when zoom changes

    def __init__(self) -> None:
        """Initialize viewport."""
        super().__init__()
        self.state = ViewportState()
        self._transform = QTransform()
        self._inverse_transform = QTransform()
        # Don't update transform yet - widget size is not set
        # It will be updated when set_widget_size is called

    # Coordinate Transformations

    def canvas_to_widget(self, x: float, y: float) -> tuple[float, float]:
        """Transform canvas coordinates to widget coordinates.

        Args:
            x: Canvas X coordinate
            y: Canvas Y coordinate

        Returns:
            Tuple of (widget_x, widget_y)
        """
        point = self._transform.map(QPointF(x, y))
        return (point.x(), point.y())

    def widget_to_canvas(self, x: float, y: float) -> tuple[float, float]:
        """Transform widget coordinates to canvas coordinates.

        Args:
            x: Widget X coordinate
            y: Widget Y coordinate

        Returns:
            Tuple of (canvas_x, canvas_y)
        """
        # Ensure _inverse_transform is a QTransform
        if not isinstance(self._inverse_transform, QTransform):
            logger.error(f"_inverse_transform is not QTransform: {type(self._inverse_transform)}")
            self._inverse_transform = QTransform()

        point = self._inverse_transform.map(QPointF(x, y))
        return (point.x(), point.y())

    def canvas_rect_to_widget(self, rect: QRectF) -> QRectF:
        """Transform canvas rectangle to widget coordinates."""
        return self._transform.mapRect(rect)

    def widget_rect_to_canvas(self, rect: QRectF) -> QRectF:
        """Transform widget rectangle to canvas coordinates."""
        # Ensure _inverse_transform is a QTransform
        if not isinstance(self._inverse_transform, QTransform):
            logger.error(f"_inverse_transform is not QTransform: {type(self._inverse_transform)}")
            self._inverse_transform = QTransform()
        return self._inverse_transform.mapRect(rect)

    # Viewport Operations

    def pan(self, dx: float, dy: float) -> None:
        """Pan viewport by delta in widget coordinates.

        Args:
            dx: Delta X in widget coordinates
            dy: Delta Y in widget coordinates
        """
        # Convert widget delta to canvas delta
        canvas_dx = dx / self.state.zoom
        canvas_dy = dy / self.state.zoom

        # Apply rotation to delta
        if self.state.rotation != 0:
            angle_rad = math.radians(-self.state.rotation)
            cos_a = math.cos(angle_rad)
            sin_a = math.sin(angle_rad)

            rotated_dx = canvas_dx * cos_a - canvas_dy * sin_a
            rotated_dy = canvas_dx * sin_a + canvas_dy * cos_a

            canvas_dx = rotated_dx
            canvas_dy = rotated_dy

        # Update center
        self.state.center_x -= canvas_dx
        self.state.center_y -= canvas_dy

        # Apply constraints if needed
        if self.state.constrain_to_canvas:
            self._constrain_to_canvas()

        self._update_transform()
        self._emit_change()

    def zoom_at(self, widget_x: float, widget_y: float, factor: float) -> None:
        """Zoom at specific widget coordinate.

        Args:
            widget_x: Widget X coordinate to zoom at
            widget_y: Widget Y coordinate to zoom at
            factor: Zoom factor (>1 to zoom in, <1 to zoom out)
        """
        # Get canvas position before zoom
        canvas_before = self.widget_to_canvas(widget_x, widget_y)

        # Apply zoom with EPS guard
        new_zoom = self.state.zoom * factor
        new_zoom = max(self.state.min_zoom, EPS, min(self.state.max_zoom, new_zoom))

        if new_zoom != self.state.zoom:
            self.state.zoom = new_zoom

            # Get canvas position after zoom
            self._update_transform()
            canvas_after = self.widget_to_canvas(widget_x, widget_y)

            # Adjust center to keep point fixed
            self.state.center_x += canvas_before[0] - canvas_after[0]
            self.state.center_y += canvas_before[1] - canvas_after[1]

            self._update_transform()
            self._emit_change()
            self.zoom_changed.emit(self.state.zoom)

    def zoom_to(self, zoom: float) -> None:
        """Set absolute zoom level.

        Args:
            zoom: New zoom level
        """
        zoom = max(self.state.min_zoom, EPS, min(self.state.max_zoom, zoom))
        if zoom != self.state.zoom:
            self.state.zoom = zoom
            self._update_transform()
            self._emit_change()
            self.zoom_changed.emit(zoom)

    def rotate(self, angle: float) -> None:
        """Rotate viewport by angle in degrees.

        Args:
            angle: Rotation angle in degrees
        """
        if not self.state.allow_rotation:
            return

        self.state.rotation = (self.state.rotation + angle) % 360
        self._update_transform()
        self._emit_change()

    def set_rotation(self, angle: float) -> None:
        """Set absolute rotation in degrees.

        Args:
            angle: Rotation angle in degrees
        """
        if not self.state.allow_rotation:
            return

        self.state.rotation = angle % 360
        self._update_transform()
        self._emit_change()

    def reset(self) -> None:
        """Reset viewport to default state."""
        self.state.center_x = self.state.widget_width / 2
        self.state.center_y = self.state.widget_height / 2
        self.state.zoom = 1.0
        self.state.rotation = 0.0
        self._update_transform()
        self._emit_change()

    def fit_to_content(
        self, content_width: float, content_height: float, padding: float = 0.1
    ) -> None:
        """Fit viewport to show all content.

        Args:
            content_width: Width of content to fit
            content_height: Height of content to fit
            padding: Padding factor (0.1 = 10% padding)
        """
        self.fit_to_rect(0.0, 0.0, content_width, content_height, padding=padding)

    def fit_to_rect(
        self,
        left: float,
        top: float,
        width: float,
        height: float,
        *,
        padding: float = 0.1,
    ) -> None:
        """Fit the viewport to a rectangle in canvas space.

        ``fit_to_content`` assumes the content starts at the canvas origin.
        A spread does not: the facing page sits beside the active one and
        may extend to negative coordinates (CT-06 §6.3), so the area to fit
        needs an origin as well as a size.

        Args:
            left: Rectangle origin X in canvas pixels
            top: Rectangle origin Y in canvas pixels
            width: Rectangle width in canvas pixels
            height: Rectangle height in canvas pixels
            padding: Padding factor (0.1 = 10% padding)
        """
        if width <= 0 or height <= 0:
            return

        # Calculate zoom to fit
        zoom_x = self.state.widget_width / (width * (1 + padding))
        zoom_y = self.state.widget_height / (height * (1 + padding))

        # Use smaller zoom to fit both dimensions
        zoom = min(zoom_x, zoom_y)
        zoom = max(self.state.min_zoom, min(self.state.max_zoom, zoom))

        # Center on content
        self.state.center_x = left + width / 2
        self.state.center_y = top + height / 2
        self.state.zoom = zoom
        self.state.rotation = 0.0

        self._update_transform()
        self._emit_change()

    # State Management

    def set_widget_size(self, width: int, height: int) -> None:
        """Update widget dimensions.

        Args:
            width: New widget width
            height: New widget height
        """
        # Only update if valid size
        if width > 0 and height > 0:
            self.state.widget_width = width
            self.state.widget_height = height
            self._update_transform()
        else:
            logger.debug(f"Ignoring invalid widget size: {width}x{height}")

    def get_transform(self) -> QTransform:
        """Get current transformation matrix."""
        return QTransform(self._transform)

    def get_state(self) -> dict[str, Any]:
        """Get viewport state as dictionary."""
        return {
            "center_x": self.state.center_x,
            "center_y": self.state.center_y,
            "zoom": self.state.zoom,
            "rotation": self.state.rotation,
            "widget_width": self.state.widget_width,
            "widget_height": self.state.widget_height,
        }

    def set_state(self, state: dict[str, Any]) -> None:
        """Set viewport state from dictionary."""
        self.state.center_x = state.get("center_x", self.state.center_x)
        self.state.center_y = state.get("center_y", self.state.center_y)
        self.state.zoom = state.get("zoom", self.state.zoom)
        self.state.rotation = state.get("rotation", self.state.rotation)
        self._update_transform()
        self._emit_change()

    def get_visible_rect(self) -> QRectF:
        """Get visible canvas rectangle."""
        # Get corners in canvas space
        tl = self.widget_to_canvas(0, 0)
        br = self.widget_to_canvas(self.state.widget_width, self.state.widget_height)

        # Handle rotation - get bounding box
        if self.state.rotation != 0:
            tr = self.widget_to_canvas(self.state.widget_width, 0)
            bl = self.widget_to_canvas(0, self.state.widget_height)

            min_x = min(tl[0], tr[0], bl[0], br[0])
            max_x = max(tl[0], tr[0], bl[0], br[0])
            min_y = min(tl[1], tr[1], bl[1], br[1])
            max_y = max(tl[1], tr[1], bl[1], br[1])

            return QRectF(min_x, min_y, max_x - min_x, max_y - min_y)
        else:
            return QRectF(tl[0], tl[1], br[0] - tl[0], br[1] - tl[1])

    # Private Methods

    def _update_transform(self) -> None:
        """Update transformation matrices."""
        # Ensure we have valid dimensions
        if self.state.widget_width <= 0 or self.state.widget_height <= 0:
            # Skip transform update if widget not yet sized
            return

        # Build transformation
        transform = QTransform()

        # Translate to widget center
        transform.translate(self.state.widget_width / 2, self.state.widget_height / 2)

        # Apply rotation if needed
        if self.state.rotation != 0 and math.isfinite(self.state.rotation):
            transform.rotate(self.state.rotation)

        # Ensure zoom is valid
        zoom = self.state.zoom
        if not (math.isfinite(zoom) and abs(zoom) > EPS):
            logger.warning(f"Viewport zoom invalid ({zoom}); clamping to 1.0")
            zoom = 1.0
            self.state.zoom = 1.0
        transform.scale(zoom, zoom)

        # Apply canvas offset with validation
        cx = self.state.center_x if math.isfinite(self.state.center_x) else 0.0
        cy = self.state.center_y if math.isfinite(self.state.center_y) else 0.0
        transform.translate(-cx, -cy)

        self._transform = transform

        # Calculate inverse transform
        try:
            result = transform.inverted()
            if isinstance(result, tuple):
                # PySide6 returns (invertible: bool, transform: QTransform or bool)
                invertible, inv = result

                # When not invertible, PySide6 returns (False, False)
                # When invertible, it returns (True, QTransform)
                if invertible and isinstance(inv, QTransform):
                    # Transform is invertible and we have a valid inverse
                    self._inverse_transform = inv
                elif abs(zoom) > EPS:
                    # Transform not invertible or invalid result
                    # Create our own inverse for simple transforms
                    # For a transform with only translate, scale, and rotate,
                    # we can manually calculate the inverse
                    inv_transform = QTransform()
                    # Reverse order of operations
                    inv_transform.translate(cx, cy)
                    inv_transform.scale(1.0 / zoom, 1.0 / zoom)
                    if self.state.rotation != 0:
                        inv_transform.rotate(-self.state.rotation)
                    inv_transform.translate(
                        -self.state.widget_width / 2,
                        -self.state.widget_height / 2,
                    )
                    self._inverse_transform = inv_transform
                else:
                    # Zoom is too small, use identity
                    self._inverse_transform = QTransform()
            elif isinstance(result, QTransform):
                # Some versions might return QTransform directly
                self._inverse_transform = result
            else:
                logger.error("Unexpected inverted() return: %r", type(result))
                self._inverse_transform = QTransform()
        except Exception as e:
            logger.error("Error calculating inverse transform: %s", e)
            self._inverse_transform = QTransform()

    def _constrain_to_canvas(self) -> None:
        """Constrain viewport to canvas bounds."""
        # This would require canvas dimensions
        # Implementation depends on canvas size management
        pass

    def _emit_change(self) -> None:
        """Emit viewport changed signal."""
        self.viewport_changed.emit(self.get_state())

    # Utility Methods

    def screen_to_canvas_distance(self, screen_distance: float) -> float:
        """Convert screen distance to canvas distance."""
        return screen_distance / self.state.zoom

    def canvas_to_screen_distance(self, canvas_distance: float) -> float:
        """Convert canvas distance to screen distance."""
        return canvas_distance * self.state.zoom
