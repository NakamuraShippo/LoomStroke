#!/usr/bin/env python
"""Dock hint overlay for visual docking feedback."""

from __future__ import annotations

from contextlib import suppress

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QMainWindow, QRubberBand, QWidget

from ._dock_safe import is_valid


class DockHintOverlay(QWidget):
    """Visual overlay showing dock drop zones."""

    def __init__(self, main_window: QMainWindow):
        """Initialize dock hint overlay.

        Args:
            main_window: Parent main window
        """
        super().__init__(main_window)

        # Configure as transparent overlay
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setWindowFlags(Qt.WindowType.SubWindow | Qt.WindowType.FramelessWindowHint)

        # Visual components
        self.band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.band.setStyleSheet("""
            QRubberBand {
                border: 2px solid rgba(0, 120, 215, 180);
                background-color: rgba(0, 120, 215, 40);
                border-radius: 4px;
            }
        """)

        # Animation for smooth transitions
        self.opacity_animation = QPropertyAnimation(self, b"windowOpacity")
        self.opacity_animation.setDuration(150)
        self.opacity_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)

        # State
        self.current_rect: QRect | None = None
        self.target_type: str | None = None

        self.hide()

    def show_rect(self, rect: QRect, target_type: str = "edge") -> None:
        """Show highlight rectangle.

        Args:
            rect: Rectangle to highlight in main window coordinates
            target_type: Type of target ('edge', 'tab', 'center')
        """
        if not is_valid(self):
            return

        if rect.isNull() or rect.isEmpty():
            self.clear()
            return

        self.current_rect = rect
        self.target_type = target_type

        # Update overlay size to match parent
        parent = self.parentWidget()
        if parent and is_valid(parent):
            self.resize(parent.size())

        # Update band geometry with style based on target type
        if target_type == "tab":
            # Thinner border for tab targets
            self.band.setStyleSheet("""
                QRubberBand {
                    border: 2px dashed rgba(0, 120, 215, 180);
                    background-color: rgba(0, 120, 215, 25);
                    border-radius: 2px;
                }
            """)
        elif target_type == "center":
            # Different color for center docking
            self.band.setStyleSheet("""
                QRubberBand {
                    border: 2px solid rgba(0, 180, 100, 180);
                    background-color: rgba(0, 180, 100, 40);
                    border-radius: 4px;
                }
            """)
        else:
            # Default edge style
            self.band.setStyleSheet("""
                QRubberBand {
                    border: 2px solid rgba(0, 120, 215, 180);
                    background-color: rgba(0, 120, 215, 40);
                    border-radius: 4px;
                }
            """)

        self.band.setGeometry(rect)

        # Show with fade-in animation
        if not self.isVisible():
            self.setWindowOpacity(0)
            self.show()
            self.opacity_animation.setStartValue(0)
            self.opacity_animation.setEndValue(1)
            self.opacity_animation.start()

        self.band.show()
        self.raise_()

    def clear(self) -> None:
        """Clear and hide overlay."""
        if not is_valid(self):
            return

        # Fade out animation
        if self.isVisible():
            self.opacity_animation.setStartValue(1)
            self.opacity_animation.setEndValue(0)
            self.opacity_animation.finished.connect(self._hide_after_fade)
            self.opacity_animation.start()
        else:
            self._hide_after_fade()

    def _hide_after_fade(self) -> None:
        """Hide overlay after fade animation completes."""
        with suppress(Exception):
            self.opacity_animation.finished.disconnect(self._hide_after_fade)

        self.band.hide()
        self.hide()
        self.current_rect = None
        self.target_type = None

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint additional visual hints.

        Args:
            event: Paint event
        """
        if not self.current_rect or not self.target_type:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw icons or arrows based on target type
        if self.target_type == "edge":
            # Draw directional arrow for edge docking
            self._draw_edge_arrow(painter, self.current_rect)
        elif self.target_type == "tab":
            # Draw tab icon for tabification
            self._draw_tab_icon(painter, self.current_rect)

    def _draw_edge_arrow(self, painter: QPainter, rect: QRect) -> None:
        """Draw directional arrow for edge docking.

        Args:
            painter: QPainter instance
            rect: Target rectangle
        """
        center = rect.center()

        # Determine arrow direction based on rect position
        parent_rect = self.rect()
        arrow_color = QColor(0, 120, 215, 120)

        painter.setPen(QPen(arrow_color, 3, Qt.PenStyle.SolidLine))
        painter.setBrush(QBrush(arrow_color))

        # Simple arrow visualization
        arrow_size = 20
        if rect.x() < parent_rect.width() // 4:  # Left edge
            # Draw arrow pointing left
            points = [
                QPoint(center.x() - arrow_size, center.y()),
                QPoint(center.x(), center.y() - arrow_size // 2),
                QPoint(center.x(), center.y() + arrow_size // 2),
            ]
        elif rect.x() > parent_rect.width() * 3 // 4:  # Right edge
            # Draw arrow pointing right
            points = [
                QPoint(center.x() + arrow_size, center.y()),
                QPoint(center.x(), center.y() - arrow_size // 2),
                QPoint(center.x(), center.y() + arrow_size // 2),
            ]
        elif rect.y() < parent_rect.height() // 4:  # Top edge
            # Draw arrow pointing up
            points = [
                QPoint(center.x(), center.y() - arrow_size),
                QPoint(center.x() - arrow_size // 2, center.y()),
                QPoint(center.x() + arrow_size // 2, center.y()),
            ]
        else:  # Bottom edge
            # Draw arrow pointing down
            points = [
                QPoint(center.x(), center.y() + arrow_size),
                QPoint(center.x() - arrow_size // 2, center.y()),
                QPoint(center.x() + arrow_size // 2, center.y()),
            ]

        painter.drawPolygon(points)

    def _draw_tab_icon(self, painter: QPainter, rect: QRect) -> None:
        """Draw tab icon for tabification.

        Args:
            painter: QPainter instance
            rect: Target rectangle
        """
        # Draw simple tab indicator
        tab_rect = QRect(rect.x() + 10, rect.y() + 5, 60, 25)

        painter.setPen(QPen(QColor(0, 120, 215, 180), 2))
        painter.setBrush(QBrush(QColor(0, 120, 215, 60)))
        painter.drawRoundedRect(tab_rect, 3, 3)

        # Draw "TAB" text
        painter.setPen(QPen(QColor(0, 120, 215, 200), 1))
        painter.drawText(tab_rect, Qt.AlignmentFlag.AlignCenter, "TAB")
