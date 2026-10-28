"""Input handler for canvas mouse and tablet events."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, cast

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPointingDevice, QTabletEvent
from PySide6.QtWidgets import QApplication, QWidget

from loomstroke.host_api import StrokePoint
from loomstroke.platform.settings_manager import get_settings_manager

from .drawing_guide_snap import snap_drawing_position
from .space_key_tracker import SpaceKeyTracker

logger = logging.getLogger(__name__)


@dataclass
class InputState:
    """Current input device state."""

    # Device info
    device_type: str = "mouse"  # mouse, pen, eraser, touch
    device_id: int = 0

    # Position
    last_x: float = 0.0
    last_y: float = 0.0

    # Pressure (for tablets)
    pressure: float = 1.0

    # Tilt (for tablets)
    tilt_x: float = 0.0
    tilt_y: float = 0.0

    # Buttons
    buttons: int = 0

    # Modifiers
    shift: bool = False
    ctrl: bool = False
    alt: bool = False
    space: bool = False

    # State
    is_pressed: bool = False
    is_hovering: bool = False

    # Timing
    last_event_time: float = 0.0


class InputHandler(QObject):
    """Handles input events for canvas widget.

    Converts Qt mouse/tablet events to unified StrokePoint format
    for tool consumption.
    """

    # Signals
    pointer_down = Signal(object)  # StrokePoint
    pointer_move = Signal(list)  # list[StrokePoint]
    pointer_up = Signal(object)  # StrokePoint
    pointer_hover = Signal(object)  # StrokePoint

    # Modifier signals
    modifier_changed = Signal(dict)  # Dict of modifiers

    def __init__(self, canvas_widget: Any) -> None:
        """Initialize input handler.

        Args:
            canvas_widget: Parent canvas widget for coordinate conversion
        """
        super().__init__()
        self.canvas = canvas_widget
        self.state = InputState()

        # Point accumulation for coalescing
        self._point_buffer: list[StrokePoint] = []
        self._last_emit_time = 0.0
        self._emit_interval = 1.0 / 240.0  # 240Hz max emit rate (reduced throttling)

        # Pressure smoothing
        self._pressure_history: list[float] = []
        self._pressure_window = 3

        # Debug mode
        self._debug = False

        # Panning support
        self._is_panning = False
        self._last_widget_pos: tuple[float, float] | None = None
        self._pan_candidate = False
        self._pan_start_pos: tuple[float, float] | None = None
        self._pan_button = Qt.MouseButton.LeftButton
        self._pan_modifier = "space"
        self._pan_drag_threshold = float(max(2, QApplication.startDragDistance()))
        self._context_menu_button = Qt.MouseButton.RightButton
        self._context_menu_modifier = "none"

        # Context menu tracking
        self._context_menu_candidate = False
        self._context_menu_start_pos: tuple[float, float] | None = None

        # Spread viewport: a press that landed on the facing page (CT-06
        # B4-c2).  Remembered so its release is dropped too -- a release
        # with no matching press would still reach the tool.
        self._stroke_input_blocked = False

        # Settings
        self._settings = get_settings_manager()
        self._apply_pan_gesture(self._settings.get("input/pan_gesture", "space_left"))
        self._apply_context_menu_gesture(
            self._settings.get("input/context_menu_gesture", "right_click")
        )
        self._perf_tablet_enabled = bool(self._settings.get("debug/input_tablet_perf", False))
        self._perf_tablet_warn_ms = float(
            cast(float, self._settings.get("debug/input_tablet_warn_ms", 20.0))
        )
        self._perf_tablet_sample_every = int(
            cast(int, self._settings.get("debug/input_tablet_sample_every", 120))
        )
        self._perf_tablet_count = 0
        self._perf_tablet_last_time = 0.0
        self._settings.settings_changed.connect(self._on_setting_changed)

        self._space_key_tracker = (
            SpaceKeyTracker(self, canvas_widget) if isinstance(canvas_widget, QWidget) else None
        )

    # Mouse Event Handlers

    def handle_mouse_press(self, event: QMouseEvent) -> None:
        """Handle mouse press event."""
        # Store widget position for panning
        pos = event.position()
        widget_pos = (pos.x(), pos.y())
        self._last_widget_pos = widget_pos
        self._pan_candidate = False
        self._pan_start_pos = None

        # Update modifiers early
        self._update_modifiers(event.modifiers())

        context_match = self._matches_context_menu_trigger(event)
        if context_match:
            self._context_menu_candidate = True
            self._context_menu_start_pos = widget_pos
        else:
            self._context_menu_candidate = False
            self._context_menu_start_pos = None

        pan_match = self._matches_pan_trigger(event)
        if pan_match:
            self._pan_candidate = True
            self._pan_start_pos = widget_pos
            self._is_panning = False

        if pan_match or context_match:
            event.accept()
            return

        # Only left button starts drawing
        if event.button() != Qt.MouseButton.LeftButton:
            event.accept()
            return

        # Update state for drawing
        self.state.device_type = "mouse"
        self.state.is_pressed = True
        self.state.pressure = 1.0
        self.state.tilt_x = 0.0
        self.state.tilt_y = 0.0

        self._store_button_state(event)

        # Get position in canvas coordinates
        canvas_pos = self.canvas.viewport.widget_to_canvas(pos.x(), pos.y())

        if self._spread_blocks_point(canvas_pos[0], canvas_pos[1]):
            # The facing page of a spread is a read-only preview: without
            # this the click would draw on the active page at the same
            # canvas coordinates (spec §6.3).
            self.state.is_pressed = False
            event.accept()
            return

        # Update position
        self.state.last_x = canvas_pos[0]
        self.state.last_y = canvas_pos[1]

        # Create stroke point for drawing
        point = self._create_stroke_point()

        # Emit signal for drawing tools
        self.pointer_down.emit(point)
        self._point_buffer.clear()
        self._last_emit_time = 0.0

        # Ensure mouse defaults restored after press
        self.state.pressure = 1.0

        if self._debug:
            logger.debug(f"Mouse press at canvas: {canvas_pos}")

    def handle_mouse_move(self, event: QMouseEvent) -> None:
        """Handle mouse move event."""
        # Get position
        pos = event.position()
        widget_pos = (pos.x(), pos.y())

        # Promote pan candidate once movement passes threshold
        if self._pan_candidate and self._pan_start_pos:
            if self._drag_distance_exceeded(self._pan_start_pos, widget_pos):
                self._is_panning = True
                self._pan_candidate = False
                self._context_menu_candidate = False
                self._context_menu_start_pos = None
            else:
                if (
                    self._context_menu_candidate
                    and self._context_menu_start_pos
                    and self._drag_distance_exceeded(self._context_menu_start_pos, widget_pos)
                ):
                    self._context_menu_candidate = False
                    self._context_menu_start_pos = None
                event.accept()
                return

        # Handle panning if in panning mode
        if self._is_panning and self._last_widget_pos:
            # Calculate delta in widget coordinates
            dx = widget_pos[0] - self._last_widget_pos[0]
            dy = widget_pos[1] - self._last_widget_pos[1]

            # Pan the viewport
            self.canvas.viewport.pan(dx, dy)

            # Update last position
            self._last_widget_pos = widget_pos

            # Request canvas update
            self.canvas.update()

            event.accept()
            return

        # Suppress hover/draw while awaiting context menu
        if self._context_menu_candidate:
            if self._context_menu_start_pos and self._drag_distance_exceeded(
                self._context_menu_start_pos,
                widget_pos,
            ):
                self._context_menu_candidate = False
                self._context_menu_start_pos = None
            event.accept()
            return

        # Normal drawing/hover mode
        canvas_pos = self.canvas.viewport.widget_to_canvas(pos.x(), pos.y())

        # Update position
        self.state.last_x = canvas_pos[0]
        self.state.last_y = canvas_pos[1]

        # Update modifiers
        self._update_modifiers(event.modifiers())

        # Mouse input uses full pressure
        self.state.pressure = 1.0
        self.state.tilt_x = 0.0
        self.state.tilt_y = 0.0

        # Create stroke point
        point = self._create_stroke_point()

        if self.state.is_pressed:
            self._emit_coalesced_move(point)
        else:
            # Hover event
            self.pointer_hover.emit(point)

    def handle_mouse_release(self, event: QMouseEvent) -> None:
        """Handle mouse release event."""
        pos = event.position()
        widget_pos = (pos.x(), pos.y())

        # Handle panning mode release
        if self._is_panning:
            self._is_panning = False
            self._last_widget_pos = None
            event.accept()
            return

        if self._pan_candidate:
            self._pan_candidate = False
            self._pan_start_pos = None
            if self._context_menu_candidate and self._is_context_menu_button(event):
                self._emit_context_menu(widget_pos)
            self._context_menu_candidate = False
            self._context_menu_start_pos = None
            event.accept()
            return

        if self._context_menu_candidate and self._is_context_menu_button(event):
            self._context_menu_candidate = False
            self._context_menu_start_pos = None
            self._emit_context_menu(widget_pos)
            event.accept()
            return

        if not self.state.is_pressed:
            return

        # Update state for drawing
        self.state.is_pressed = False

        self._store_button_state(event)

        # Get position
        canvas_pos = self.canvas.viewport.widget_to_canvas(pos.x(), pos.y())

        # Update position
        self.state.last_x = canvas_pos[0]
        self.state.last_y = canvas_pos[1]

        # Flush any remaining points
        if self._point_buffer:
            self.pointer_move.emit(self._point_buffer.copy())
            self._point_buffer.clear()

        # Create final point
        point = self._create_stroke_point()

        # Emit signal for drawing tools
        self.pointer_up.emit(point)

        if self._debug:
            logger.debug(f"Mouse release at canvas: {canvas_pos}")

    # Tablet Event Handlers

    def handle_tablet_event(self, event: QTabletEvent) -> None:
        """Handle tablet event."""
        if self._perf_tablet_enabled:
            self._record_tablet_interval(event)
        self._handle_tablet_pointer_type(event)
        point = self._update_tablet_state(event)
        self._dispatch_tablet_point(event, point)

    def _handle_tablet_pointer_type(self, event: QTabletEvent) -> None:
        """Update input state based on pointer type."""
        pointer_value = event.pointerType()
        # PySide6 6.x exposes pointer types via QPointingDevice.PointerType.
        # The legacy QTabletEvent.PointerType_Pen and Qt.TabletPointerType
        # paths are kept as fallbacks for older Qt versions.
        modern_enum = getattr(QPointingDevice, "PointerType", None)
        pen_enum = getattr(modern_enum, "Pen", None) if modern_enum else None
        if pen_enum is None:
            pen_enum = getattr(QTabletEvent, "PointerType_Pen", None)
        if pen_enum is None:
            tablet_enum = getattr(Qt, "TabletPointerType", None)
            if tablet_enum is not None:
                pen_enum = getattr(tablet_enum, "Pen", None)
        eraser_enum = getattr(modern_enum, "Eraser", None) if modern_enum else None
        if eraser_enum is None:
            eraser_enum = getattr(QTabletEvent, "PointerType_Eraser", None)
        if eraser_enum is None:
            tablet_enum = getattr(Qt, "TabletPointerType", None)
            if tablet_enum is not None:
                eraser_enum = getattr(tablet_enum, "Eraser", None)

        if self._matches_pointer_type(pointer_value, pen_enum):
            self.state.device_type = "pen"
        elif self._matches_pointer_type(pointer_value, eraser_enum):
            self.state.device_type = "eraser"
        else:
            self.state.device_type = "touch"

    def _matches_pointer_type(self, pointer_value: Any, target: object | None) -> bool:
        if target is None:
            return False
        try:
            if pointer_value == target:
                return True
        except (TypeError, ValueError):
            logger.debug("pointer equality check failed", exc_info=True)
        try:
            if int(pointer_value) == int(cast(int, target)):
                return True
        except (TypeError, ValueError):
            logger.debug("pointer int coercion failed", exc_info=True)
        pointer_name = getattr(pointer_value, "name", None)
        target_name = getattr(target, "name", None) or str(target)
        return bool(pointer_name and target_name and pointer_name.lower() == target_name.lower())

    def _update_tablet_state(self, event: QTabletEvent) -> StrokePoint:
        pos = event.position()
        canvas_pos = self.canvas.viewport.widget_to_canvas(pos.x(), pos.y())
        self.state.last_x = canvas_pos[0]
        self.state.last_y = canvas_pos[1]
        self.state.pressure = event.pressure()
        if event.xTilt() != 0 or event.yTilt() != 0:
            self.state.tilt_x = event.xTilt()
            self.state.tilt_y = event.yTilt()
        self._update_modifiers(event.modifiers())
        self._smooth_pressure()
        return self._create_stroke_point()

    def _dispatch_tablet_point(self, event: QTabletEvent, point: StrokePoint) -> None:
        if event.type() == QTabletEvent.Type.TabletPress:
            if self._spread_blocks_point(self.state.last_x, self.state.last_y):
                self._stroke_input_blocked = True
                event.accept()
                return
            self._stroke_input_blocked = False
            self.state.is_pressed = True
            self.pointer_down.emit(point)
            self._point_buffer.clear()
            self._last_emit_time = 0.0

        elif event.type() == QTabletEvent.Type.TabletMove:
            if self.state.is_pressed:
                self._emit_coalesced_move(point)
            else:
                self.pointer_hover.emit(point)

        elif event.type() == QTabletEvent.Type.TabletRelease:
            if self._stroke_input_blocked:
                self._stroke_input_blocked = False
                event.accept()
                return
            self.state.is_pressed = False
            if self._point_buffer:
                self.pointer_move.emit(self._point_buffer.copy())
                self._point_buffer.clear()
            self.pointer_up.emit(point)

        # Accept event to prevent mouse event generation
        event.accept()

    # Helper Methods

    def _emit_coalesced_move(self, point: StrokePoint) -> None:
        now = time.time()
        if now - self._last_emit_time >= self._emit_interval:
            if self._point_buffer:
                self._point_buffer.append(point)
                self.pointer_move.emit(self._point_buffer.copy())
                self._point_buffer.clear()
            else:
                self.pointer_move.emit([point])
            self._last_emit_time = now
        else:
            self._point_buffer.append(point)

    def _store_button_state(self, event: QMouseEvent) -> None:
        """Record the pressed buttons as an int across PySide6 versions."""

        buttons = event.buttons()
        if hasattr(buttons, "value"):
            self.state.buttons = int(buttons.value)
        elif isinstance(buttons, int):
            self.state.buttons = buttons
        else:
            try:
                self.state.buttons = int(cast(int, buttons))
            except (TypeError, ValueError):
                # Last resort: just store as-is or use 0
                logger.warning(f"Could not convert mouse buttons to int: {type(buttons)}")
                self.state.buttons = 0

    def _spread_blocks_point(self, x: float, y: float) -> bool:
        """Whether a canvas point belongs to the spread's facing page.

        Only tool input is gated: pan, zoom and the context menu stay
        available over the facing page, because navigating a spread from
        either side is the point of showing both.
        """
        blocks = getattr(self.canvas, "spread_blocks_canvas_point", None)
        if not callable(blocks):
            return False
        try:
            return bool(blocks(x, y))
        except Exception:
            logger.debug("spread input gate unavailable", exc_info=True)
            return False

    def _create_stroke_point(self) -> StrokePoint:
        """Create stroke point from current state."""
        # Get current time in milliseconds
        current_time = time.time() * 1000

        # Build tilt tuple if available
        tilt = None
        if self.state.tilt_x != 0 or self.state.tilt_y != 0:
            tilt = (self.state.tilt_x, self.state.tilt_y)

        return StrokePoint(
            pos=snap_drawing_position(self.canvas, self.state.last_x, self.state.last_y),
            pressure=self.state.pressure,
            tilt=tilt,
            time=current_time,
        )

    def _update_modifiers(self, modifiers: Qt.KeyboardModifier) -> None:
        """Update modifier key states."""
        old_shift = self.state.shift
        old_ctrl = self.state.ctrl
        old_alt = self.state.alt

        self.state.shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        self.state.ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        self.state.alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)

        # Emit signal if modifiers changed
        if (
            self.state.shift != old_shift
            or self.state.ctrl != old_ctrl
            or self.state.alt != old_alt
        ):
            self.modifier_changed.emit(
                {"shift": self.state.shift, "ctrl": self.state.ctrl, "alt": self.state.alt}
            )

    def _smooth_pressure(self) -> None:
        """Apply pressure smoothing for tablets."""
        # Add to history
        self._pressure_history.append(self.state.pressure)

        # Limit history size
        if len(self._pressure_history) > self._pressure_window:
            self._pressure_history.pop(0)

        # Calculate smoothed pressure (simple average)
        if self._pressure_history:
            self.state.pressure = sum(self._pressure_history) / len(self._pressure_history)

    def _on_setting_changed(self, key: str, value: object) -> None:
        if key == "input/pan_gesture":
            self._apply_pan_gesture(value)
        if key == "input/context_menu_gesture":
            self._apply_context_menu_gesture(value)
        if key == "debug/input_tablet_perf":
            self._perf_tablet_enabled = bool(value)
        if key == "debug/input_tablet_warn_ms":
            try:
                self._perf_tablet_warn_ms = float(cast(float, value))
            except (TypeError, ValueError):
                self._perf_tablet_warn_ms = 20.0
        if key == "debug/input_tablet_sample_every":
            try:
                self._perf_tablet_sample_every = max(1, int(cast(int, value)))
            except (TypeError, ValueError):
                self._perf_tablet_sample_every = 120

    def _apply_pan_gesture(self, value: object) -> None:
        normalized = str(value).strip().lower()
        normalized = normalized.replace("+", "_").replace("-", "_").replace(" ", "_")
        mapping = {
            "right": (Qt.MouseButton.RightButton, "none"),
            "right_drag": (Qt.MouseButton.RightButton, "none"),
            "middle": (Qt.MouseButton.MiddleButton, "none"),
            "middle_drag": (Qt.MouseButton.MiddleButton, "none"),
            "space_left": (Qt.MouseButton.LeftButton, "space"),
            "alt_left": (Qt.MouseButton.LeftButton, "alt"),
            "shift_left": (Qt.MouseButton.LeftButton, "shift"),
            "ctrl_left": (Qt.MouseButton.LeftButton, "ctrl"),
        }
        button, modifier = mapping.get(normalized, mapping["space_left"])
        self._pan_button = button
        self._pan_modifier = modifier

    def _apply_context_menu_gesture(self, value: object) -> None:
        normalized = str(value).strip().lower()
        normalized = normalized.replace("+", "_").replace("-", "_").replace(" ", "_")
        mapping = {
            "right_click": (Qt.MouseButton.RightButton, "none"),
            "right": (Qt.MouseButton.RightButton, "none"),
            "middle_click": (Qt.MouseButton.MiddleButton, "none"),
            "middle": (Qt.MouseButton.MiddleButton, "none"),
            "space_left_click": (Qt.MouseButton.LeftButton, "space"),
            "alt_left_click": (Qt.MouseButton.LeftButton, "alt"),
            "shift_left_click": (Qt.MouseButton.LeftButton, "shift"),
            "ctrl_left_click": (Qt.MouseButton.LeftButton, "ctrl"),
        }
        button, modifier = mapping.get(normalized, mapping["right_click"])
        self._context_menu_button = button
        self._context_menu_modifier = modifier

    def _matches_pan_trigger(self, event: QMouseEvent) -> bool:
        if event.button() != self._pan_button:
            return False
        return self._pan_modifier_matches()

    def _matches_context_menu_trigger(self, event: QMouseEvent) -> bool:
        if event.button() != self._context_menu_button:
            return False
        return self._context_menu_modifier_matches()

    def _pan_modifier_matches(self) -> bool:
        if self._pan_modifier == "none":
            return not (self.state.shift or self.state.ctrl or self.state.alt or self.state.space)
        if self._pan_modifier == "space":
            return self.state.space
        if self._pan_modifier == "alt":
            return self.state.alt
        if self._pan_modifier == "ctrl":
            return self.state.ctrl
        if self._pan_modifier == "shift":
            return self.state.shift
        return False

    def _context_menu_modifier_matches(self) -> bool:
        if self._context_menu_modifier == "none":
            return not (self.state.shift or self.state.ctrl or self.state.alt or self.state.space)
        if self._context_menu_modifier == "space":
            return self.state.space
        if self._context_menu_modifier == "alt":
            return self.state.alt
        if self._context_menu_modifier == "ctrl":
            return self.state.ctrl
        if self._context_menu_modifier == "shift":
            return self.state.shift
        return False

    def _is_context_menu_button(self, event: QMouseEvent) -> bool:
        return event.button() == self._context_menu_button

    def _drag_distance_exceeded(
        self,
        start: tuple[float, float],
        current: tuple[float, float],
    ) -> bool:
        dx = current[0] - start[0]
        dy = current[1] - start[1]
        return (dx * dx + dy * dy) >= (self._pan_drag_threshold * self._pan_drag_threshold)

    def _emit_context_menu(self, widget_pos: tuple[float, float]) -> None:
        requester = getattr(self.canvas, "request_context_menu", None)
        if callable(requester):
            requester(widget_pos)

    def is_pan_gesture(self, event: QMouseEvent) -> bool:
        """Return True if the event should trigger panning."""
        self._update_modifiers(event.modifiers())
        return self._matches_pan_trigger(event)

    def is_context_menu_gesture(self, event: QMouseEvent) -> bool:
        """Return True if the event should trigger context menu."""
        self._update_modifiers(event.modifiers())
        return self._matches_context_menu_trigger(event)

    def _record_tablet_interval(self, event: QTabletEvent) -> None:
        now = time.monotonic()
        event_type = event.type()
        if event_type == QTabletEvent.Type.TabletPress:
            self._perf_tablet_count = 0
            self._perf_tablet_last_time = now
            return
        if event_type == QTabletEvent.Type.TabletRelease:
            self._perf_tablet_last_time = 0.0
            return
        if event_type != QTabletEvent.Type.TabletMove:
            return

        if self._perf_tablet_last_time:
            dt_ms = (now - self._perf_tablet_last_time) * 1000.0
            if dt_ms >= self._perf_tablet_warn_ms:
                logger.warning("Tablet interval %.1fms (pressed=%s)", dt_ms, self.state.is_pressed)
            elif self._perf_tablet_sample_every and (
                self._perf_tablet_count % self._perf_tablet_sample_every == 0
            ):
                logger.info("Tablet interval sample %.1fms", dt_ms)
        self._perf_tablet_count += 1
        self._perf_tablet_last_time = now

    # Public API

    def get_coalesced_points(self) -> list[StrokePoint]:
        """Get accumulated points for coalescing.

        Returns current buffer and clears it.
        """
        points = self._point_buffer.copy()
        self._point_buffer.clear()
        return points

    def reset(self) -> None:
        """Reset input handler state."""
        self.state = InputState()
        self._point_buffer.clear()
        self._pressure_history.clear()
        self._is_panning = False
        self._last_widget_pos = None
        self._pan_candidate = False
        self._pan_start_pos = None
        self._context_menu_candidate = False
        self._context_menu_start_pos = None
        self._perf_tablet_count = 0
        self._perf_tablet_last_time = 0.0

    def set_debug(self, enabled: bool) -> None:
        """Enable/disable debug logging."""
        self._debug = enabled

    def get_device_info(self) -> dict[str, Any]:
        """Get current input device information."""
        return {
            "type": self.state.device_type,
            "id": self.state.device_id,
            "has_pressure": self.state.device_type in ["pen", "eraser"],
            "has_tilt": self.state.tilt_x != 0 or self.state.tilt_y != 0,
            "is_pressed": self.state.is_pressed,
        }

    def is_tablet_active(self) -> bool:
        """Check if tablet device is active."""
        return self.state.device_type in ["pen", "eraser"]

    def handle_key_press(self, event: QKeyEvent) -> None:
        """Handle key press event for modifier tracking."""
        # Track Space key for panning mode
        if event.key() == Qt.Key.Key_Space:
            self.state.space = True
            event.accept()

    def handle_key_release(self, event: QKeyEvent) -> None:
        """Handle key release event for modifier tracking."""
        # Release Space key tracking
        if event.key() == Qt.Key.Key_Space:
            if event.isAutoRepeat():
                event.accept()
                return
            self.state.space = False
            # If we were panning, stop it
            if self._is_panning:
                self._is_panning = False
                self._last_widget_pos = None
            event.accept()

    def _mk_point(self, x: float, y: float, pressure: float = 1.0) -> StrokePoint:
        """Helper to create a StrokePoint.

        Args:
            x: Canvas X coordinate
            y: Canvas Y coordinate
            pressure: Pressure value (0-1)

        Returns:
            StrokePoint with current timestamp
        """
        current_time = time.time() * 1000  # Convert to milliseconds

        # Build tilt tuple if available
        tilt = None
        if self.state.tilt_x != 0 or self.state.tilt_y != 0:
            tilt = (self.state.tilt_x, self.state.tilt_y)

        return StrokePoint(
            pos=snap_drawing_position(self.canvas, x, y),
            pressure=pressure,
            tilt=tilt,
            time=current_time,
        )
