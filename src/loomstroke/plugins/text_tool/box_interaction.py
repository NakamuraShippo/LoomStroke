"""Screen-space hit testing and pointer routing for editable text boxes."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QTransform

from loomstroke.plugins.sdk.transform import (
    handle_positions_for_bounds,
    hit_test_handle_points,
)


def clear_hover_cursor(owner: Any) -> None:
    from loomstroke.plugins.sdk.cursors import apply_hover_cursor  # noqa: PLC0415

    owner._hover_cursor_set = apply_hover_cursor(None, is_set=owner._hover_cursor_set)
    canvas = owner._canvas_widget()
    if canvas is not None:
        canvas.unsetCursor()


def box_control_at(
    x: float,
    y: float,
    bounds: tuple[float, float, float, float],
    transform: QTransform,
) -> tuple[str, str] | None:
    """Prioritize handles, then a three-pixel border; leave the interior free."""
    point = transform.map(QPointF(x, y))
    handles = {
        name: transform.map(QPointF(*position))
        for name, position in handle_positions_for_bounds(bounds, include_rotate=False).items()
    }
    top = handles["edge-top"]
    center = (handles["corner-tl"] + handles["corner-br"]) * 0.5
    direction = top - center
    length = math.hypot(direction.x(), direction.y())
    direction = direction / length if length > 1e-6 else QPointF(0, -1)
    handles["rotate"] = top + direction * 12.0
    hit = hit_test_handle_points(
        point.x(), point.y(), {name: (p.x(), p.y()) for name, p in handles.items()}
    )
    if hit is not None:
        return hit
    corners = [handles[name] for name in ("corner-tl", "corner-tr", "corner-br", "corner-bl")]
    for start, end in zip(corners, corners[1:] + corners[:1], strict=True):
        delta = end - start
        squared = delta.x() ** 2 + delta.y() ** 2
        if squared < 1e-12:
            continue
        relative = point - start
        t = max(0.0, min(1.0, (relative.x() * delta.x() + relative.y() * delta.y()) / squared))
        distance = point - (start + delta * t)
        if math.hypot(distance.x(), distance.y()) <= 3.0:
            return ("move-area", "move")
    return None


def send_selection_pointer(owner: Any, phase: str, point: tuple[float, float]) -> bool:
    """Continue a drag that entered editing through the canvas's initial press."""
    editor = owner._inline_editor
    canvas = owner._inline_canvas_widget
    if editor is None or canvas is None:
        return False
    if phase != "press" and not getattr(editor, "_h_dragging", False):
        return False
    widget_point = QPointF(*canvas.viewport.canvas_to_widget(*point))
    local = editor.viewport().mapFrom(canvas, widget_point)
    event_type = {
        "press": QEvent.Type.MouseButtonPress,
        "move": QEvent.Type.MouseMove,
        "release": QEvent.Type.MouseButtonRelease,
    }[phase]
    QCoreApplication.sendEvent(
        editor.viewport(),
        QMouseEvent(
            event_type,
            local,
            editor.viewport().mapToGlobal(local),
            Qt.MouseButton.NoButton if phase == "move" else Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton if phase == "release" else Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    return True
