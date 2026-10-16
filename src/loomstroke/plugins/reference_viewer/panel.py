"""Reference image viewer panel widget.

Allows users to load one or more reference images and view them in a
dockable panel with zoom, pan, and opacity controls.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("ReferenceViewerPanel", text, use_fallback=True)


class _ImageView(QWidget):
    """Zoomable / pannable image display area."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._scale: float = 1.0
        self._offset = QPointF(0, 0)
        self._drag_start: QPointF | None = None
        self._drag_offset = QPointF(0, 0)
        self._opacity: float = 1.0
        self.setMinimumSize(100, 100)
        self.setMouseTracking(True)

    def set_image(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self._scale = 1.0
        self._offset = QPointF(0, 0)
        self._fit_to_view()
        self.update()

    def set_opacity(self, value: float) -> None:
        self._opacity = max(0.0, min(1.0, value))
        self.update()

    def _fit_to_view(self) -> None:
        if self._pixmap is None or self._pixmap.isNull():
            return
        pw, ph = self._pixmap.width(), self._pixmap.height()
        vw, vh = self.width(), self.height()
        if pw == 0 or ph == 0 or vw == 0 or vh == 0:
            return
        self._scale = min(vw / pw, vh / ph)
        self._offset = QPointF(
            (vw - pw * self._scale) / 2,
            (vh - ph * self._scale) / 2,
        )

    def paintEvent(self, event: object) -> None:  # noqa: N802
        if self._pixmap is None or self._pixmap.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setOpacity(self._opacity)
        pw = self._pixmap.width() * self._scale
        ph = self._pixmap.height() * self._scale
        target = QRectF(self._offset.x(), self._offset.y(), pw, ph)
        p.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
        p.end()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else 1 / 1.1
        self._scale = max(0.05, min(20.0, self._scale * factor))
        self.update()

    def mousePressEvent(self, event: object) -> None:  # noqa: N802
        if hasattr(event, "button") and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position()
            self._drag_offset = QPointF(self._offset)

    def mouseMoveEvent(self, event: object) -> None:  # noqa: N802
        if self._drag_start is not None and hasattr(event, "position"):
            delta = event.position() - self._drag_start
            self._offset = self._drag_offset + delta
            self.update()

    def mouseReleaseEvent(self, event: object) -> None:  # noqa: N802
        self._drag_start = None

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        if self._pixmap is not None:
            self._fit_to_view()


class ReferenceViewerPanel(QWidget):
    """Panel for loading and viewing reference images."""

    _IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tiff);;All Files (*)"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._images: list[tuple[str, QPixmap]] = []
        self._current_index: int = -1
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Toolbar
        toolbar = QHBoxLayout()
        self._open_btn = QPushButton(_ts("Open"))
        self._open_btn.setToolTip(_ts("Load a reference image"))
        self._open_btn.clicked.connect(self._on_open)
        toolbar.addWidget(self._open_btn)

        self._prev_btn = QPushButton("<")
        self._prev_btn.setFixedWidth(28)
        self._prev_btn.clicked.connect(self._on_prev)
        toolbar.addWidget(self._prev_btn)

        self._index_label = QLabel("0/0")
        self._index_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._index_label.setFixedWidth(50)
        toolbar.addWidget(self._index_label)

        self._next_btn = QPushButton(">")
        self._next_btn.setFixedWidth(28)
        self._next_btn.clicked.connect(self._on_next)
        toolbar.addWidget(self._next_btn)

        self._remove_btn = QPushButton(_ts("Remove"))
        self._remove_btn.clicked.connect(self._on_remove)
        toolbar.addWidget(self._remove_btn)

        layout.addLayout(toolbar)

        # Image view
        self._view = _ImageView(self)
        layout.addWidget(self._view, 1)

        # Opacity slider
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel(_ts("Opacity")))
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(100)
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        opacity_row.addWidget(self._opacity_slider)
        layout.addLayout(opacity_row)

        self._update_nav()

    def _on_open(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, _ts("Open Reference Image"), "", self._IMAGE_FILTER
        )
        for path in paths:
            self._load_image(path)

    def _load_image(self, path: str) -> None:
        img = QImage(path)
        if img.isNull():
            logger.warning("Failed to load reference image: %s", path)
            return
        self.add_image(Path(path).name, img)

    def add_image(self, name: str, image: QImage) -> None:
        """Display an already decoded image without opening a file dialog."""
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        self._images.append((name, pixmap))
        self._current_index = len(self._images) - 1
        self._show_current()

    def _show_current(self) -> None:
        if 0 <= self._current_index < len(self._images):
            _, pixmap = self._images[self._current_index]
            self._view.set_image(pixmap)
        else:
            self._view.set_image(None)
        self._update_nav()

    def _update_nav(self) -> None:
        total = len(self._images)
        idx = self._current_index + 1 if total > 0 else 0
        self._index_label.setText(f"{idx}/{total}")
        self._prev_btn.setEnabled(self._current_index > 0)
        self._next_btn.setEnabled(self._current_index < total - 1)
        self._remove_btn.setEnabled(total > 0)

    def _on_prev(self) -> None:
        if self._current_index > 0:
            self._current_index -= 1
            self._show_current()

    def _on_next(self) -> None:
        if self._current_index < len(self._images) - 1:
            self._current_index += 1
            self._show_current()

    def _on_remove(self) -> None:
        if 0 <= self._current_index < len(self._images):
            self._images.pop(self._current_index)
            if self._current_index >= len(self._images):
                self._current_index = len(self._images) - 1
            self._show_current()

    def _on_opacity_changed(self, value: int) -> None:
        self._view.set_opacity(value / 100.0)
