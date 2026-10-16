#!/usr/bin/env python
"""Dock hit testing for drop zone detection."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QDockWidget, QMainWindow

from ._dock_safe import is_valid

# Configuration constants
EDGE_MARGIN = 40  # Edge detection sensitivity (pixels)
TAB_TITLE_HEIGHT = 30  # Tab title bar height for tab detection
MIN_DOCK_SIZE = 150  # Minimum size for dock zones
CENTER_ZONE_SIZE = 100  # Size of center drop zone


def hit_test(main_window: QMainWindow, global_pos: QPoint) -> dict[str, Any]:
    """Test for dock drop zones at given position.

    Args:
        main_window: Main window to test against
        global_pos: Global cursor position

    Returns:
        Dict with:
            - kind: 'left'/'right'/'top'/'bottom'/'center'/'tab' or None
            - target: Target QDockWidget for 'tab' kind, None otherwise
            - rect: Highlight rectangle in main window coordinates
    """
    if not is_valid(main_window):
        return {"kind": None, "target": None, "rect": QRect()}

    # Convert to main window coordinates
    local_pos = main_window.mapFromGlobal(global_pos)
    window_rect = main_window.rect()

    # 1. Check for center docking (floating position)
    center_rect = QRect(
        window_rect.center().x() - CENTER_ZONE_SIZE // 2,
        window_rect.center().y() - CENTER_ZONE_SIZE // 2,
        CENTER_ZONE_SIZE,
        CENTER_ZONE_SIZE,
    )
    if center_rect.contains(local_pos):
        return {"kind": "center", "target": None, "rect": center_rect}

    # 2. Check edge docking zones
    edge_result = _check_edge_zones(window_rect, local_pos)
    if edge_result["kind"]:
        return edge_result

    # 3. Check for tab docking on existing docks
    tab_result = _check_tab_zones(main_window, local_pos)
    if tab_result["kind"]:
        return tab_result

    return {"kind": None, "target": None, "rect": QRect()}


def _check_edge_zones(window_rect: QRect, local_pos: QPoint) -> dict[str, Any]:
    """Check if position is in edge dock zones.

    Args:
        window_rect: Main window rectangle
        local_pos: Local cursor position

    Returns:
        Dict with dock zone information
    """
    # Define edge detection zones
    edge_zones = {
        "left": QRect(
            window_rect.left(),
            window_rect.top() + EDGE_MARGIN,
            EDGE_MARGIN,
            window_rect.height() - 2 * EDGE_MARGIN,
        ),
        "right": QRect(
            window_rect.right() - EDGE_MARGIN,
            window_rect.top() + EDGE_MARGIN,
            EDGE_MARGIN,
            window_rect.height() - 2 * EDGE_MARGIN,
        ),
        "top": QRect(
            window_rect.left() + EDGE_MARGIN,
            window_rect.top(),
            window_rect.width() - 2 * EDGE_MARGIN,
            EDGE_MARGIN,
        ),
        "bottom": QRect(
            window_rect.left() + EDGE_MARGIN,
            window_rect.bottom() - EDGE_MARGIN,
            window_rect.width() - 2 * EDGE_MARGIN,
            EDGE_MARGIN,
        ),
    }

    # Check each edge zone
    for edge_kind, detection_rect in edge_zones.items():
        if detection_rect.contains(local_pos):
            # Create visual highlight rectangle (larger than detection zone)
            highlight_rect = _create_edge_highlight(window_rect, edge_kind)
            return {"kind": edge_kind, "target": None, "rect": highlight_rect}

    return {"kind": None, "target": None, "rect": QRect()}


def _create_edge_highlight(window_rect: QRect, edge_kind: str) -> QRect:
    """Create highlight rectangle for edge docking.

    Args:
        window_rect: Main window rectangle
        edge_kind: Type of edge ('left', 'right', 'top', 'bottom')

    Returns:
        Highlight rectangle
    """
    if edge_kind == "left":
        return QRect(
            window_rect.left(),
            window_rect.top(),
            max(window_rect.width() // 3, MIN_DOCK_SIZE),
            window_rect.height(),
        )
    elif edge_kind == "right":
        width = max(window_rect.width() // 3, MIN_DOCK_SIZE)
        return QRect(window_rect.right() - width, window_rect.top(), width, window_rect.height())
    elif edge_kind == "top":
        return QRect(
            window_rect.left(),
            window_rect.top(),
            window_rect.width(),
            max(window_rect.height() // 3, MIN_DOCK_SIZE),
        )
    elif edge_kind == "bottom":
        height = max(window_rect.height() // 3, MIN_DOCK_SIZE)
        return QRect(window_rect.left(), window_rect.bottom() - height, window_rect.width(), height)
    else:
        return QRect()


def _check_tab_zones(main_window: QMainWindow, local_pos: QPoint) -> dict[str, Any]:
    """Check if position is over a dock widget tab area.

    Args:
        main_window: Main window containing docks
        local_pos: Local cursor position

    Returns:
        Dict with dock zone information
    """
    # Find all dock widgets
    for dock in main_window.findChildren(QDockWidget):
        if not dock or not is_valid(dock):
            continue

        if not dock.isVisible() or dock.isFloating():
            continue

        # Get dock geometry in main window coordinates
        try:
            dock_rect = dock.rect()
            dock_pos = dock.mapTo(main_window, dock_rect.topLeft())
            dock_rect_in_main = QRect(dock_pos, dock_rect.size())

            # Check if cursor is over tab title area
            tab_area = QRect(
                dock_rect_in_main.x(),
                dock_rect_in_main.y(),
                dock_rect_in_main.width(),
                TAB_TITLE_HEIGHT,
            )

            if tab_area.contains(local_pos):
                # Return full dock area as highlight
                return {"kind": "tab", "target": dock, "rect": dock_rect_in_main}
        except (RuntimeError, AttributeError):
            # Widget might have been deleted
            continue

    return {"kind": None, "target": None, "rect": QRect()}


def get_drop_area(kind: str) -> Qt.DockWidgetArea | None:
    """Convert drop kind to Qt dock widget area.

    Args:
        kind: Drop zone kind

    Returns:
        Qt.DockWidgetArea or None
    """
    area_map = {
        "left": Qt.DockWidgetArea.LeftDockWidgetArea,
        "right": Qt.DockWidgetArea.RightDockWidgetArea,
        "top": Qt.DockWidgetArea.TopDockWidgetArea,
        "bottom": Qt.DockWidgetArea.BottomDockWidgetArea,
    }
    return area_map.get(kind)
