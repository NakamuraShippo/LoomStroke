"""A keyboard-accessible settings disclosure that keeps its child widgets alive."""

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QSizePolicy, QToolButton, QVBoxLayout, QWidget

from loomstroke.ui.translations import translated_literal_domain


class SettingsSection(QWidget):
    expandedChanged = Signal(bool)

    def __init__(
        self,
        source_title: str,
        parent: QWidget | None = None,
        *,
        expanded: bool = False,
        domain: str = "BasicPen",
    ) -> None:
        super().__init__(parent)
        self._source_title, self._domain = source_title, domain
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = QToolButton(self)
        self.header.setCheckable(True)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.setStyleSheet(
            "QToolButton { text-align: left; font-weight: 600; padding: 6px; }"
        )
        layout.addWidget(self.header)
        self.content = QWidget(self)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(8, 6, 8, 8)
        self.content_layout.setSpacing(6)
        layout.addWidget(self.content)
        self.header.toggled.connect(self._toggled)
        self.set_expanded(expanded)
        self.retranslate()

    @property
    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        with QSignalBlocker(self.header):
            self.header.setChecked(expanded)
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        self.content.setVisible(self.is_expanded)
        arrow = (
            Qt.ArrowType.DownArrow
            if self.is_expanded
            else (
                Qt.ArrowType.LeftArrow
                if self.layoutDirection() == Qt.LayoutDirection.RightToLeft
                else Qt.ArrowType.RightArrow
            )
        )
        self.header.setArrowType(arrow)

    def _toggled(self, expanded: bool) -> None:
        self._sync_visibility()
        self.expandedChanged.emit(expanded)

    def retranslate(self) -> None:
        text = translated_literal_domain(self._domain, self._source_title, use_fallback=True)
        self.header.setText(text)
        self.header.setAccessibleName(text)
        self.header.setToolTip(text)
        self._sync_visibility()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() in (QEvent.Type.LanguageChange, QEvent.Type.LayoutDirectionChange):
            self.retranslate()
