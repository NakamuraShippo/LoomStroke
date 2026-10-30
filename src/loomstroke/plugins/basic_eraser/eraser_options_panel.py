# Eraser options panel
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFormLayout, QLabel, QSlider, QWidget

from loomstroke.ui.widget_translations import bind_widget_text

__all__ = ["EraserOptionsPanel", "create_eraser_panel"]


class EraserOptionsPanel(QWidget):
    changed = Signal(dict)  # {"size": int, "strength": float}

    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        lay = QFormLayout(self)

        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(1, 200)
        self.strength = QSlider(Qt.Horizontal)
        self.strength.setRange(1, 100)
        self.hardness = QSlider(Qt.Horizontal)
        self.hardness.setRange(0, 100)

        # Load initial values from Settings (source of truth: tools.basic_eraser)
        saved = settings.get("tools.basic_eraser", {}) or {}
        self.size.setValue(int(saved.get("size", 20.0)))
        self.strength.setValue(int(saved.get("opacity", 1.0) * 100))
        self.hardness.setValue(int(saved.get("hardness", 0.5) * 100))

        lay.addRow(bind_widget_text(QLabel(), "Size", domain="EraserOptionsPanel"), self.size)
        lay.addRow(
            bind_widget_text(QLabel(), "Strength", domain="EraserOptionsPanel"), self.strength
        )
        lay.addRow(
            bind_widget_text(QLabel(), "Hardness", domain="EraserOptionsPanel"), self.hardness
        )

        for w in (self.size, self.strength, self.hardness):
            w.valueChanged.connect(self._emit_changed)

    def _emit_changed(self, *_):
        payload = {
            "size": self.size.value(),
            "opacity": self.strength.value() / 100.0,  # Map strength to opacity
            "hardness": self.hardness.value() / 100.0,
        }
        # Save to Settings (source of truth: tools.basic_eraser)
        current = dict(self._settings.get("tools.basic_eraser", {}) or {})
        current.update(
            {
                "size": payload["size"],
                "opacity": payload["opacity"],
                "hardness": payload["hardness"],
            }
        )
        self._settings.set("tools.basic_eraser", current)
        self.changed.emit(payload)


def create_eraser_panel(settings):
    """
    Factory function to create eraser options panel.

    Args:
        settings: Settings manager instance

    Returns:
        EraserOptionsPanel instance
    """
    return EraserOptionsPanel(settings)
