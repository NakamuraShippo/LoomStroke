"""Perspective-specific controls backed by the existing assist command."""

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QWidget

from loomstroke.core.drawing_assist.perspective_edit import with_point_count
from loomstroke.ui.translations import translated_literal_domain


def _ts(text):
    return translated_literal_domain("DrawingAssistPanel", text, use_fallback=True)


class PerspectiveControls(QWidget):
    def __init__(self, panel, assist):
        super().__init__(panel.form_host)
        self.panel = panel
        self.count = QComboBox()
        self.axis = QComboBox()
        self.count_label, self.axis_label = QLabel(), QLabel()
        self.edit = QCheckBox()
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addRow(self.count_label, self.count)
        layout.addRow(self.axis_label, self.axis)
        layout.addRow(self.edit)
        layout.addRow(self.hint)
        for n in (1, 2, 3):
            self.count.addItem("", n)
        count = len(assist.points("vanishing_points"))
        self.count.setCurrentIndex(count - 1)
        for value in ("auto", "vertical", *(f"vp{i + 1}" for i in range(count))):
            self.axis.addItem("", value)
        self.axis.setCurrentIndex(self.axis.findData(assist.option("axis", "auto")))
        handles = panel._controller.handles
        self.edit.setChecked(handles.enabled)
        active = panel._controller.active_assist()
        self.edit.setEnabled(active is not None and active.id == assist.id)
        self.count.currentIndexChanged.connect(self._count_changed)
        self.axis.currentIndexChanged.connect(self._axis_changed)
        self.edit.toggled.connect(self._edit_changed)
        self.retranslate()

    def retranslate(self):
        self.count_label.setText(_ts("Perspective ruler"))
        for i, text in enumerate(
            ("1-point perspective", "2-point perspective", "3-point perspective")
        ):
            self.count.setItemText(i, _ts(text))
        self.axis_label.setText(_ts("Drawing direction"))
        for i in range(self.axis.count()):
            value = self.axis.itemData(i)
            self.axis.setItemText(
                i, _ts({"auto": "Automatic", "vertical": "Vertical"}.get(value, value.upper()))
            )
        self.edit.setText(_ts("Edit vanishing points"))
        self.hint.setText(_ts("Drag VP handles on the canvas. Esc cancels the drag."))

    def changeEvent(self, event):
        if event.type() == QEvent.LanguageChange:
            self.retranslate()
        super().changeEvent(event)

    def _count_changed(self):
        assist = self.panel.edited_assist()
        if assist is not None:
            self.panel.submit_assist(
                with_point_count(
                    assist, self.count.currentData(), self.panel._controller.canvas_size()
                )
            )

    def _axis_changed(self):
        assist = self.panel.edited_assist()
        if assist is not None:
            self.panel.submit_assist(assist.with_options(axis=self.axis.currentData()))

    def _edit_changed(self, checked):
        handles = self.panel._controller.handles
        handles.enabled = checked
        handles.cancel()


def open_perspective_ruler(window):
    from .drawing_assist_panel import DrawingAssistPanel  # noqa: PLC0415

    panel = window.findChild(DrawingAssistPanel)
    if panel is None:
        return
    dock = window._panel_docks.get("drawing_assist_panel")
    if dock is not None:
        dock.show()
        dock.raise_()
    panel.type_combo.setCurrentIndex(panel.type_combo.findData("perspective"))
    for assist in panel._controller.state().items:
        if assist.type == "perspective":
            panel.select(assist.id)
            break
