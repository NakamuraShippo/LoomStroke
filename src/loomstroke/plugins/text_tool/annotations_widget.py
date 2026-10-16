"""Selection annotation controls embedded in the Font panel."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .typography_widget import _ts


class TextAnnotationsWidget(QWidget):
    """Collect annotation options; range validation and editing stay in the tool."""

    annotation_requested = Signal(str, dict)

    def __init__(self, parent: QWidget | None = None) -> None:  # noqa: PLR0915
        super().__init__(parent)
        self._state: Any = None
        self._labels: list[tuple[Any, str]] = []
        self.controls: dict[str, Any] = {}
        self._choices: list[tuple[QComboBox, list[tuple[str, str]]]] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.heading = QToolButton(self)
        self.heading.setCheckable(True)
        self.heading.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.heading)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(160)
        self.scroll.setMaximumHeight(310)
        self.page = QWidget(self.scroll)
        self.scroll.setWidget(self.page)
        layout.addWidget(self.scroll)
        form = QFormLayout(self.page)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.hint = QLabel(self.page)
        self.hint.setWordWrap(True)
        form.addRow(self.hint)
        self.controls["ruby"] = QLineEdit(self.page)
        self._row(form, "Reading", self.controls["ruby"])
        self._combo(
            form,
            "position",
            "Position",
            [("before", "Before (above / right)"), ("after", "After (below / left)")],
        )
        self._combo(
            form,
            "align",
            "Ruby alignment",
            [
                ("center", "Center"),
                ("start", "Start"),
                ("end", "End"),
                ("space-around", "Space around"),
                ("space-between", "Space between"),
            ],
        )
        self._spin(form, "size_ratio", "Size ratio", 0.1, 2.0, 0.5)
        self._spin(form, "gap_em", "Gap", 0.0, 2.0, 0.05)
        self._spin(form, "overhang_em", "Allowed overhang", 0.0, 2.0, 0.5)
        self.controls["use_ruby_glyphs"] = QCheckBox(self.page)
        self._row(form, "Use ruby-specific glyphs", self.controls["use_ruby_glyphs"])
        self._spin(form, "jidori_width_em", "Character distribution width", 0.5, 100.0, 4.0)
        self._spin(form, "warichu_size_ratio", "Inline note size ratio", 0.1, 0.5, 0.5)
        self._spin(form, "warichu_gap_em", "Inline note line gap", 0.0, 0.5, 0.0)
        self._combo(
            form,
            "emphasis_mark",
            "Emphasis mark",
            [("auto", "Automatic"), ("sesame", "Sesame"), ("dot", "Dot"), ("circle", "Circle")],
        )
        self._combo(
            form,
            "emphasis_position",
            "Emphasis position",
            [("before", "Before (above / right)"), ("after", "After (below / left)")],
        )
        self._spin(form, "emphasis_size_ratio", "Emphasis size ratio", 0.1, 2.0, 0.5)
        self._spin(form, "emphasis_gap_em", "Emphasis gap", 0.0, 2.0, 0.0)
        self.buttons: dict[tuple[str, str], QPushButton] = {}
        for kind, sources in (
            ("ruby", ("Apply ruby", "Remove ruby")),
            ("tcy", ("Apply tate-chu-yoko", "Remove tate-chu-yoko")),
            ("emphasis", ("Apply emphasis", "Remove emphasis")),
            ("warichu", ("Apply two-line note", "Remove two-line note")),
            ("jidori", ("Apply character distribution", "Remove character distribution")),
        ):
            row = QHBoxLayout()
            for action, source in zip(("set", "remove"), sources, strict=True):
                button = QPushButton(self.page)
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                button.clicked.connect(lambda _=False, k=kind, a=action: self._request(k, a))
                self.buttons[(kind, action)] = button
                self._labels.append((button, source))
                row.addWidget(button)
            form.addRow(row)
        self.mono_button = QPushButton(self.page)
        self.mono_button.clicked.connect(lambda: self._request("ruby", "set", per_character=True))
        self._labels.append(
            (self.mono_button, "Apply per-character ruby (readings separated by |)")
        )
        form.addRow(self.mono_button)
        self.heading.toggled.connect(self._expand)
        self._expand(False)
        self.set_selection({}, vertical=False)
        self.refresh_translations()

    def _row(self, form, source, widget) -> None:
        label = QLabel(self.page)
        label.setWordWrap(True)
        self._labels.append((label, source))
        form.addRow(label, widget)

    def _combo(self, form, key, source, choices) -> None:
        widget = QComboBox(self.page)
        for value, label in choices:
            widget.addItem(label, value)
        self.controls[key] = widget
        self._choices.append((widget, choices))
        self._row(form, source, widget)

    def _spin(self, form, key, source, lower, upper, default) -> None:
        widget = QDoubleSpinBox(self.page)
        widget.setRange(lower, upper)
        widget.setDecimals(2)
        widget.setSingleStep(0.05)
        widget.setKeyboardTracking(False)
        if key.endswith("_em"):
            widget.setSuffix(" em")
        widget.setValue(default)
        self.controls[key] = widget
        self._row(form, source, widget)

    def _request(self, kind: str, action: str, *, per_character=False) -> None:
        request: dict[str, Any] = {"action": action}
        if kind in {"warichu", "jidori"} and action == "set":
            request.update(
                {
                    key.removeprefix(kind + "_"): widget.value()
                    for key, widget in self.controls.items()
                    if key.startswith(kind + "_")
                }
            )
        if kind in {"ruby", "emphasis"} and action == "set":
            for key, widget in self.controls.items():
                if key.startswith(("jidori_", "warichu_")):
                    continue
                is_emphasis = key.startswith("emphasis_")
                if is_emphasis != (kind == "emphasis"):
                    continue
                option = key.removeprefix("emphasis_")
                if isinstance(widget, QLineEdit):
                    request[option] = widget.text()
                elif isinstance(widget, QCheckBox):
                    request[option] = widget.isChecked()
                elif isinstance(widget, QComboBox):
                    request[option] = widget.currentData()
                else:
                    request[option] = widget.value()
        if per_character:
            request["per_character"] = True
        self.annotation_requested.emit(kind, request)

    def set_selection(self, state: dict[str, Any], *, vertical: bool) -> None:
        snapshot = (state, vertical)
        if snapshot == self._state:
            return
        self._state = deepcopy(snapshot)
        enabled = bool(state.get("enabled", False))
        self.mono_button.setEnabled(enabled)
        for widget in self.controls.values():
            widget.setEnabled(enabled)
        for (kind, action), button in self.buttons.items():
            allowed = action == "remove" or (
                not state.get(f"{kind}_error") and (kind != "tcy" or vertical)
            )
            button.setEnabled(enabled and allowed)
        ruby = state.get("ruby_runs", {})
        defaults = {
            "ruby": "",
            "position": "before",
            "align": "center",
            "size_ratio": 0.5,
            "gap_em": 0.05,
            "overhang_em": 0.5,
            "use_ruby_glyphs": False,
        }
        for key, widget in self.controls.items():
            if key.startswith(("jidori_", "warichu_")):
                kind, name = key.split("_", 1)
                default = {"width_em": 4.0, "size_ratio": 0.5, "gap_em": 0.0}[name]
                value = state.get(kind + "_runs", {}).get(name, default)
            elif key.startswith("emphasis_"):
                name = key.removeprefix("emphasis_")
                value = state.get("emphasis_runs", {}).get(
                    name,
                    {"mark": "auto", "position": "before", "size_ratio": 0.5, "gap_em": 0.0}[name],
                )
            else:
                value = ruby.get(key, defaults[key])
            if isinstance(widget, QLineEdit):
                widget.setText(value)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(value)
            elif isinstance(widget, QComboBox):
                widget.setCurrentIndex(widget.findData(value))
            else:
                widget.setValue(value)
        self._refresh_hint()

    def _refresh_hint(self) -> None:
        state = self._state[0] if self._state is not None else {}
        errors = [state.get(key) for key in ("error", "ruby_error", "tcy_error")]
        if "annotation_cross_range" in errors:
            source = (
                "Ruby may contain a whole tate-chu-yoko range; partial overlap is not supported."
            )
        elif "annotation_invalid" in errors:
            source = "Enter a reading and select characters within one paragraph."
        else:
            source = "Select text on the canvas to add or remove an annotation."
        self.hint.setText(_ts(source))

    def _expand(self, expanded: bool) -> None:
        self.scroll.setVisible(expanded)
        self.heading.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def refresh_translations(self) -> None:
        self.heading.setText(_ts("Ruby, tate-chu-yoko and emphasis"))
        self._refresh_hint()
        for widget, source in self._labels:
            widget.setText(_ts(source))
        for widget, choices in self._choices:
            for index, (_, source) in enumerate(choices):
                widget.setItemText(index, _ts(source))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
