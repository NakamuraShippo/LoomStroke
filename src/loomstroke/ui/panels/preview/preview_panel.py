"""
Preview panel - combines view and presenter components.

Part of the View/Presenter pattern split for preview panel.
Per Master.md A3 specifications.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from numpy.typing import NDArray
from PySide6.QtCore import QRectF, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget

from loomstroke.ui.typing import Signal0, Signal1, Signal2

from .preview_presenter import PreviewPresenter
from .preview_view import PreviewView

logger = logging.getLogger(__name__)


class PreviewPanel(QWidget):
    """
    Main preview panel that coordinates view and presenter.

    Implements View/Presenter pattern for separation of concerns.
    """

    # Public signals
    zoom_changed = Signal(float)
    viewport_drag_requested = Signal(float, float)
    image_displayed = Signal(int, int)
    fit_requested = Signal()
    reset_zoom_requested = Signal()

    def __init__(self, parent: QWidget | None = None, event_bus: Any | None = None):
        """
        Initialize preview panel.

        Args:
            parent: Parent widget
            event_bus: EventBus instance for communication
        """
        super().__init__(parent)

        # Create presenter
        self.presenter: PreviewPresenter = PreviewPresenter(event_bus)

        # Create view
        self.view: PreviewView = PreviewView(self)

        # Set view as main widget layout
        _view_layout = self.view.layout()
        if _view_layout is not None:
            self.setLayout(_view_layout)

        # Connect view and presenter
        self._connect_components()

        logger.info("PreviewPanel initialized with View/Presenter pattern")

    def _connect_components(self) -> None:
        """Connect view and presenter signals."""
        # View -> Presenter connections
        self.view.fit_requested.connect(self.fit_requested.emit)
        self.view.reset_zoom_requested.connect(self.reset_zoom_requested.emit)
        self.view.pan_requested.connect(self.viewport_drag_requested.emit)

        # Presenter -> View connections
        self.presenter.image_changed.connect(self._on_image_changed)
        self.presenter.display_cleared.connect(self.view.clear_display)

        # Presenter -> External connections
        self.view.zoom_value_changed.connect(self.zoom_changed.emit)

    @Slot(QPixmap, int, int)
    def _on_image_changed(self, pixmap: QPixmap, width: int, height: int) -> None:
        """
        Handle image change from presenter.

        Args:
            pixmap: New image pixmap
            width: Image width
            height: Image height
        """
        self.view.display_image(pixmap, width, height)
        self.image_displayed.emit(width, height)

    # Public API methods (for backwards compatibility)

    def set_image(self, image_data: NDArray[Any]) -> None:
        """
        Set the preview image.

        Args:
            image_data: Image as numpy array (H, W, C)
        """
        self.presenter.set_image(image_data)

    def set_image_from_qpixmap(self, pixmap: QPixmap) -> None:
        """Set preview image from QPixmap."""
        self.presenter.set_image_from_qpixmap(pixmap)

    def set_image_from_bytes(self, image_bytes: bytes) -> None:
        """
        Set preview image from bytes.

        Args:
            image_bytes: Image data as bytes
        """
        self.presenter.set_image_from_bytes(image_bytes)

    def clear_preview(self) -> None:
        """Clear the preview."""
        self.presenter.clear_preview()

    def get_current_image(self) -> NDArray[Any] | None:
        """
        Get the current preview image.

        Returns:
            Image data or None
        """
        return self.presenter.get_current_image()

    def fit_to_view(self) -> None:
        """Fit image to view."""
        self.view.fit_to_view()

    def reset_zoom(self) -> None:
        """Reset zoom to 100%."""
        self.view.reset_zoom()

    def cleanup(self) -> None:
        """Clean up resources."""
        self.presenter.cleanup()
        logger.debug("PreviewPanel cleanup complete")

    def set_viewport_indicator(self, rect: QRectF | None) -> None:
        """Update the visible canvas area rectangle on preview."""
        self.view.set_viewport_rect(rect)


if TYPE_CHECKING:  # pragma: no cover - typing aid only
    PreviewPanel.zoom_changed = cast(  # type: ignore[assignment]
        Signal1[float], PreviewPanel.zoom_changed
    )
    PreviewPanel.fit_requested = cast(  # type: ignore[assignment]
        Signal0, PreviewPanel.fit_requested
    )
    PreviewPanel.reset_zoom_requested = cast(  # type: ignore[assignment]
        Signal0, PreviewPanel.reset_zoom_requested
    )
    PreviewPanel.viewport_drag_requested = cast(  # type: ignore[assignment]
        Signal2[float, float],
        PreviewPanel.viewport_drag_requested,
    )
    PreviewPanel.image_displayed = cast(  # type: ignore[assignment]
        Signal2[int, int], PreviewPanel.image_displayed
    )
