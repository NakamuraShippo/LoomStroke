"""
Pen tool options panel.
Per Master.md specifications, provides settings UI for pen tool.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFormLayout, QSlider, QWidget

__all__ = ["PenOptionsPanel", "create_pen_panel"]


class PenOptionsPanel(QWidget):
    """Options panel for pen tool settings."""

    changed = Signal(dict)  # {"size": int, "opacity": float, "hardness": float}

    def __init__(self, settings):
        """Initialize pen options panel."""
        super().__init__()
        self._settings = settings
        lay = QFormLayout(self)

        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(1, 200)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(1, 100)
        self.hardness = QSlider(Qt.Horizontal)
        self.hardness.setRange(0, 100)

        # 初期値は Settings から
        self.size.setValue(int(settings.get("pen/size", 12)))
        self.opacity.setValue(int(settings.get("pen/opacity", 100)))
        self.hardness.setValue(int(settings.get("pen/hardness", 80)))

        lay.addRow("Size", self.size)
        lay.addRow("Opacity", self.opacity)
        lay.addRow("Hardness", self.hardness)

        for w in (self.size, self.opacity, self.hardness):
            w.valueChanged.connect(self._emit_changed)

    def _emit_changed(self, *_):
        """Emit changed signal with current values."""
        payload = {
            "size": self.size.value(),
            "opacity": self.opacity.value() / 100.0,
            "hardness": self.hardness.value() / 100.0,
        }
        # Settings に保存（デバウンスしても良い）  # noqa: RUF003
        self._settings.set("pen/size", payload["size"])
        self._settings.set("pen/opacity", int(payload["opacity"] * 100))
        self._settings.set("pen/hardness", int(payload["hardness"] * 100))
        self.changed.emit(payload)


def create_pen_panel(settings):
    """
    Factory function to create pen options panel.

    Args:
        settings: Settings manager instance

    Returns:
        PenOptionsPanel instance
    """
    return PenOptionsPanel(settings)
