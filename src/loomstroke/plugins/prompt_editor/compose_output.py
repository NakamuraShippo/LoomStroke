"""Prompt Editor output section: collapsed summary, full text, Copy and Tidy with LLM."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

_MONO = "font-family: 'Cascadia Mono', Consolas, monospace;"


class OutputSection(QWidget):
    """Shows the composed prompt; collapsed by default to a one-line summary.

    The panel passes translated ``texts`` (``count`` contains ``{count}``) and its
    handlers; this widget only displays the text it is given.
    """

    def __init__(
        self,
        parent: QWidget,
        *,
        texts: dict[str, str],
        register: Callable[[Callable[[str], None], str], None],
        on_copy: Callable[[], None],
        on_tidy: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self._count_template = "{count}"
        self._text = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.toggle_button = QToolButton(self)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle_button.setAutoRaise(True)
        register(self.toggle_button.setText, texts["output"])
        self.toggle_button.toggled.connect(self.set_expanded)
        row.addWidget(self.toggle_button)

        self.summary_label = QLabel(self)
        self.summary_label.setStyleSheet(_MONO)
        self.summary_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.summary_label, 1)

        self.count_label = QLabel(self)
        row.addWidget(self.count_label)

        self.copy_button = QToolButton(self)
        register(self.copy_button.setText, texts["copy"])
        register(self.copy_button.setToolTip, texts["copy"])
        self.copy_button.clicked.connect(on_copy)
        row.addWidget(self.copy_button)

        self.tidy_button = QToolButton(self)
        register(self.tidy_button.setText, texts["tidy"])
        self.tidy_button.clicked.connect(on_tidy)
        row.addWidget(self.tidy_button)
        layout.addLayout(row)

        self.full_text = QPlainTextEdit(self)
        self.full_text.setReadOnly(True)
        self.full_text.setStyleSheet(_MONO)
        self.full_text.setMaximumHeight(140)
        self.full_text.hide()
        layout.addWidget(self.full_text)
        register(self._set_count_template, texts["count"])

    def _set_count_template(self, template: str) -> None:
        self._count_template = template
        self.set_text(self._text)

    def set_expanded(self, expanded: bool) -> None:
        if self.toggle_button.isChecked() != expanded:
            self.toggle_button.setChecked(expanded)
            return
        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.full_text.setVisible(expanded)
        self.summary_label.setVisible(not expanded)

    def set_text(self, text: str) -> None:
        self._text = text
        self.full_text.setPlainText(text)
        summary = text.replace("\r\n", "\n").replace("\n", " \u21b5 ")
        metrics = self.summary_label.fontMetrics()
        width = max(40, self.summary_label.width())
        self.summary_label.setText(metrics.elidedText(summary, Qt.TextElideMode.ElideRight, width))
        self.summary_label.setToolTip(text)
        self.count_label.setText(self._count_template.replace("{count}", str(len(text))))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.set_text(self._text)
