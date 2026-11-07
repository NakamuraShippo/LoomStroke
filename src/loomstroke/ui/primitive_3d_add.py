"""Basic-shape creation controls for the existing 3D panel."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QMessageBox, QPushButton, QWidget

from loomstroke.core.assets_3d.primitive_shapes import PRIMITIVE_KINDS, PRIMITIVE_NAMES
from loomstroke.core.commands_3d_primitive import AddPrimitive3DCommand

from .translations import translated_literal_domain

logger = logging.getLogger(__name__)


def _ts(source: str) -> str:
    return translated_literal_domain("ThreeDPrimitiveAdd", source, use_fallback=True)


class Primitive3DAddWidget(QWidget):
    """Select a stable primitive kind and create one history-backed layer."""

    def __init__(self, host_getter: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host_getter = host_getter
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.shape = QComboBox(self)
        for kind in PRIMITIVE_KINDS:
            self.shape.addItem(_ts(PRIMITIVE_NAMES[kind]), kind)
        self.add_button = QPushButton(_ts("Add basic shape"), self)
        self.add_button.clicked.connect(self.add_shape)
        layout.addWidget(self.shape, 1)
        layout.addWidget(self.add_button)

    def add_shape(self) -> bool:
        host = self._host_getter()
        if host is None:
            return False
        try:
            if host.canvas.submit(AddPrimitive3DCommand(str(self.shape.currentData()))):
                return True
        except (ValueError, RuntimeError, OSError):
            logger.warning("3D basic shape creation failed", exc_info=True)
        QMessageBox.warning(self, _ts("Basic shape"), _ts("Could not add basic shape"))
        return False

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "shape"):
            for index, kind in enumerate(PRIMITIVE_KINDS):
                self.shape.setItemText(index, _ts(PRIMITIVE_NAMES[kind]))
            self.add_button.setText(_ts("Add basic shape"))
