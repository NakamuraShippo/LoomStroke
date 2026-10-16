"""Source-preserving labels for the existing panel layout controls."""

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QLabel, QPushButton

from .frame_controls import ts


def _register(panel, widget, source):
    panel._panel_sources.append((widget, source))
    widget.setText(ts(source))
    return widget


def panel_label(panel, source):
    label = _register(panel, QLabel(), source)
    label.setWordWrap(True)
    return label


def panel_button(panel, source):
    return _register(panel, QPushButton(), source)


def retranslate_panel(panel, templates):
    for widget, source in panel._panel_sources:
        widget.setText(ts(source))
    for index, source in enumerate(("Panels", "Balloons", "Dialogue")):
        panel.tabs.setTabText(index, ts(source))
    with QSignalBlocker(panel.template_combo):
        for index in range(panel.template_combo.count()):
            panel.template_combo.setItemText(
                index, ts(templates[panel.template_combo.itemData(index)])
            )
