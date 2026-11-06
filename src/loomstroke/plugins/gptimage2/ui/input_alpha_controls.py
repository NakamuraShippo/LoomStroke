"""Per-submission transparency controls for GPT Image input images."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QWidget

from loomstroke.ui.translations import translated_literal_domain
from loomstroke.ui.widgets.property_widget_factory import ColorButton


class InputAlphaControls(QWidget):
    """Keep the source image untouched and configure an opaque upload background."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.checkbox = QCheckBox(self)
        self.color_button = ColorButton(self)
        self.color_button.set_color(QColor("#ffffff"))
        self.color_button.setEnabled(False)
        self.checkbox.toggled.connect(self.color_button.setEnabled)
        layout.addWidget(self.checkbox, 1)
        layout.addWidget(self.color_button)
        self.refresh_translations()

    def color_name(self) -> str:
        red, green, blue, _alpha = self.color_button.color()
        return QColor.fromRgbF(red, green, blue).name()

    def restore(self, enabled: bool, color: str) -> None:
        self.color_button.set_color(QColor(color))
        self.checkbox.setChecked(enabled)

    def refresh_translations(self) -> None:
        def tr(source: str) -> str:
            return translated_literal_domain("InputAlphaControls", source, use_fallback=True)

        self.checkbox.setText(tr("Fill input transparency"))
        self.checkbox.setToolTip(
            tr(
                "Fill transparent areas of input images with this color before sending. "
                "Source layers are unchanged."
            )
        )
        self.color_button.setToolTip(tr("Input fill color"))
        self.color_button.setAccessibleName(tr("Input fill color"))
