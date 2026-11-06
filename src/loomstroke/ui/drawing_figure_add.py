"""Drawing figure creation controls hosted by the existing 3D preview panel."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QMessageBox, QPushButton, QVBoxLayout, QWidget

from loomstroke.core.drawing_figures.add_command import AddDrawingFigureCommand
from loomstroke.core.drawing_figures.presets import PRESETS, preset_pose

from .translations import translated_literal_domain


def figure_text(source: str) -> str:
    return translated_literal_domain("DrawingFigure", source, use_fallback=True)


class DrawingFigureAddWidget(QWidget):
    def __init__(self, host_getter: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host_getter = host_getter
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        choices = QHBoxLayout()
        layout.addLayout(choices)
        self.model = QComboBox(self)
        for label, value in (("BodyKun", "bodykun"), ("BodyChan", "bodychan")):
            self.model.addItem(label, value)
        self.preset = QComboBox(self)
        for key, source in PRESETS.items():
            self.preset.addItem(figure_text(source), key)
        self.add_button = QPushButton(figure_text("Add drawing figure"), self)
        self.add_button.clicked.connect(self.add_figure)
        choices.addWidget(self.model)
        choices.addWidget(self.preset)
        layout.addWidget(self.add_button)

    def add_figure(self) -> bool:
        host = self._host_getter()
        if host is None:
            return False
        pose = preset_pose(str(self.model.currentData()), str(self.preset.currentData()))
        try:
            command = AddDrawingFigureCommand(pose)
            if not host.canvas.submit(command):
                raise RuntimeError(figure_text("Could not add drawing figure"))
            return True
        except (ValueError, RuntimeError, OSError) as error:
            QMessageBox.warning(
                self,
                figure_text("Drawing figure"),
                figure_text("Could not add drawing figure") + "\n" + str(error),
            )
            return False

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.add_button.setText(figure_text("Add drawing figure"))
            for index, source in enumerate(PRESETS.values()):
                self.preset.setItemText(index, figure_text(source))
