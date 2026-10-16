"""One compact typography editor shared by the balloon and dialogue tabs."""

import logging

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.translations import translated_literal_domain

from .formatting import apply_format
from .service import text_props_from_object

logger = logging.getLogger(__name__)


def _ts(source):
    return translated_literal_domain("ComicFormatting", source, use_fallback=True)


class ComicFormattingWidget(QWidget):
    def __init__(self, tool, parent=None):  # noqa: PLR0915 - linear widget construction
        super().__init__(parent)
        self.tool = tool
        self.target = None
        self._updating = False
        self.controls = {}
        self.labels = []
        self.choices = []
        self._signature = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.heading = QToolButton()
        self.heading.setCheckable(True)
        self.heading.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.heading)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(150)
        self.scroll.setMaximumHeight(290)
        layout.addWidget(self.scroll)
        page = QWidget()
        form = QFormLayout(page)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.scroll.setWidget(page)
        self.status = QLabel()
        self.status.setWordWrap(True)
        form.addRow(self.status)
        family = self._combo(form, "font_family", "Font", [])
        family.setEditable(True)
        family.addItem("", "")
        for name in QFontDatabase.families():
            family.addItem(name, name)
        family.lineEdit().editingFinished.connect(
            lambda: self._apply({"font_family": self._family_value()})
        )
        self._spin(form, "font_size_pt", "Size (pt)", 1, 512, 1)
        self._combo(form, "vertical", "Writing", [(True, "Vertical"), (False, "Horizontal")])
        self._spin(form, "line_height", "Line height", 0.5, 4, 0.1)
        self._spin(form, "tracking", "Tracking", -64, 128, 0.5)
        self.details_button = QToolButton()
        self.details_button.setCheckable(True)
        self.details_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        form.addRow(self.details_button)
        self.details = QWidget()
        detail = QFormLayout(self.details)
        detail.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.addRow(self.details)
        self._combo(detail, "font_style", "Font style", [])
        self._combo(
            detail,
            "align",
            "Alignment",
            [
                (v, s)
                for v, s in (
                    ("left", "Left"),
                    ("center", "Center"),
                    ("right", "Right"),
                    ("justify", "Justify"),
                )
            ],
        )
        self._combo(
            detail, "vertical_flow", "Column flow", [("right", "Rightward"), ("left", "Leftward")]
        )
        self._combo(detail, "word_wrap", "Wrap", [(True, "On"), (False, "Off")])
        for key, source in (
            ("padding_top", "Padding top (px)"),
            ("padding_right", "Padding right (px)"),
            ("padding_bottom", "Padding bottom (px)"),
            ("padding_left", "Padding left (px)"),
        ):
            self._spin(detail, key, source, 0, 256, 1)
        self.color = QPushButton()
        self.color.clicked.connect(self._pick_color)
        self._row(detail, "Text color", self.color)
        self.heading.toggled.connect(
            lambda checked: self._expand(self.heading, self.scroll, checked)
        )
        self.details_button.toggled.connect(
            lambda checked: self._expand(self.details_button, self.details, checked)
        )
        self._expand(self.heading, self.scroll, False)
        self._expand(self.details_button, self.details, False)
        self.retranslate()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._canvas_changed)
        canvas = getattr(tool.host_api, "canvas", None)
        signal = getattr(canvas, "composited_changed", None)
        if signal is not None:
            signal.connect(self._queue_refresh)

    def _queue_refresh(self, *_):
        if self.isVisible() and not self._refresh_timer.isActive():
            self._refresh_timer.start(0)

    def _family_value(self):
        family = self.controls["font_family"]
        index = family.currentIndex()
        if index >= 0 and family.currentText() == family.itemText(index):
            return family.itemData(index)
        return family.currentText()

    def _canvas_changed(self):
        state = self.tool.read_state()
        bid = self.tool.selected_balloon_id
        obj = state.text_objects.get(bid) if state else None
        if (bid, id(obj)) != self._signature:
            self.sync(state)

    @staticmethod
    def _expand(button, content, checked):
        content.setVisible(checked)
        button.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def _row(self, form, source, widget):
        label = QLabel()
        label.setWordWrap(True)
        self.labels.append((label, source))
        form.addRow(label, widget)

    def _combo(self, form, key, source, values):
        widget = QComboBox()
        widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        widget.setMinimumContentsLength(6)
        for value, text in values:
            widget.addItem(text, value)
        self.choices.append((widget, values))
        widget.activated.connect(lambda _: self._apply({key: widget.currentData()}))
        self.controls[key] = widget
        self._row(form, source, widget)
        return widget

    def _spin(self, form, key, source, lower, upper, step):
        widget = QDoubleSpinBox()
        widget.setRange(lower, upper)
        widget.setDecimals(2)
        widget.setSingleStep(step)
        widget.setKeyboardTracking(False)
        widget.editingFinished.connect(lambda: self._apply({key: widget.value()}))
        self.controls[key] = widget
        self._row(form, source, widget)

    def sync(self, state=None):
        if self._updating:
            return
        self._updating = True
        try:
            state = state if state is not None else self.tool.read_state()
            bid = self.tool.selected_balloon_id
            self._signature = (bid, id(state.text_objects.get(bid) if state else None))
            pages = getattr(self.tool.host_api, "pages", None)
            getter = getattr(pages, "get_active_page_id", None)
            self.target = (
                (getter(), bid) if callable(getter) and state and bid in state.balloons else None
            )
            self.scroll.widget().setEnabled(self.target is not None)
            self.status.setText(
                _ts("Balloon: {id}").format(id=bid)
                if self.target
                else _ts("Select a balloon or a dialogue row.")
            )
            if self.target is None:
                return
            props = text_props_from_object(state.text_objects.get(bid))
            style = self.controls["font_style"]
            style.clear()
            style.addItem(_ts("Default style"), "")
            for name in QFontDatabase.styles(props.font_family):
                style.addItem(name, name)
            for key, widget in self.controls.items():
                value = getattr(props, key)
                if isinstance(widget, QComboBox):
                    index = widget.findData(value)
                    if index < 0:
                        widget.addItem(str(value), value)
                        index = widget.count() - 1
                    widget.setCurrentIndex(index)
                else:
                    widget.setValue(value)
            self.color.setText(QColor(*props.color_rgba).name(QColor.NameFormat.HexArgb))
        finally:
            self._updating = False

    def _apply(self, changes):
        if self._updating or self.target is None:
            return
        try:
            apply_format(self.tool, *self.target, changes)
            self.sync()
        except Exception:
            logger.exception("Could not apply comic text format")
            self.sync()
            self.status.setText(
                _ts("Could not apply formatting. Reselect the balloon and try again.")
            )

    def _pick_color(self):
        target = self.target
        if target is None:
            return
        color = QColorDialog.getColor(
            QColor(self.color.text()),
            self,
            _ts("Text color"),
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if color.isValid() and self.target == target:
            self._apply({"color_rgba": color.getRgb()})

    def retranslate(self):
        self.heading.setText(_ts("Text formatting"))
        self.details_button.setText(_ts("More formatting"))
        self.controls["font_family"].setItemText(0, _ts("Default font"))
        for label, source in self.labels:
            label.setText(_ts(source))
        for widget, values in self.choices:
            for index, (_, source) in enumerate(values):
                widget.setItemText(index, _ts(source))

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
            self.sync()
