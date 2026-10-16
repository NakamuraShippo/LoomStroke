"""Focused action menus for the AI Select toolbar; original action widgets stay authoritative."""

from collections.abc import Callable, Iterable
from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from loomstroke.ui.translations import translated_literal_domain

_PREFIX = "sam3_ai_select."
_NAVIGATION = ("run", "run.status", "candidate.previous", "candidate.status", "candidate.next")
_TITLES = {
    "candidate": "Candidate Actions",
    "operation": "Operation",
    "apply": "Apply Options",
    "generation": "Generation",
    "export": "Export",
    "other": "Other Settings",
}


class SamSelectToolbarPresentation(QWidget):
    """Arrange existing buttons into primary actions and bounded menu surfaces."""

    def __init__(
        self, parent: QWidget, specs: Iterable[Any], create_widget: Callable[[Any], QWidget]
    ) -> None:
        super().__init__(parent)
        self.specs = {spec.key: spec for spec in specs}
        self.widgets = {key: create_widget(spec) for key, spec in self.specs.items()}
        self.menu_buttons: dict[str, QToolButton] = {}
        self.menu_widgets: dict[str, list[QWidget]] = {}
        self._scrolls: dict[str, QScrollArea] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(3)
        navigation, actions = QHBoxLayout(), QHBoxLayout()
        navigation.setSpacing(6)
        actions.setSpacing(6)
        root.addLayout(navigation)
        root.addLayout(actions)
        remaining = dict(self.widgets)
        for key in _NAVIGATION:
            widget = remaining.pop(_PREFIX + key, None)
            if widget is not None:
                navigation.addWidget(widget)
        apply = remaining.pop(_PREFIX + "apply.current", None)
        summary = remaining.pop(_PREFIX + "candidate.summary", None)
        grouped: dict[str, list[QWidget]] = {}
        for key, widget in remaining.items():
            grouped.setdefault(self._group(key), []).append(widget)
        for group in _TITLES:
            if group in grouped:
                button = self._make_menu(group, grouped[group])
                (navigation if group == "candidate" else actions).addWidget(button)
            if group == "operation" and apply is not None:
                actions.addWidget(apply)
        navigation.addStretch(1)
        actions.addStretch(1)
        if isinstance(summary, QLabel):
            summary.setWordWrap(True)
            summary.setMinimumWidth(0)
            summary.setMaximumWidth(480)
            summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        if summary is not None:
            root.addWidget(summary)
        self.retranslate()

    @staticmethod
    def accepts(specs: Iterable[Any]) -> bool:
        keys = {spec.key for spec in specs}
        return {_PREFIX + "run", _PREFIX + "apply.current"} <= keys and all(
            isinstance(key, str) and key.startswith(_PREFIX) for key in keys
        )

    @staticmethod
    def _group(key: str) -> str:
        suffix = key.removeprefix(_PREFIX)
        if suffix in ("candidate.reject", "refine.current"):
            return "candidate"
        if suffix.startswith("operation."):
            return "operation"
        if suffix.startswith("export."):
            return "export"
        if suffix.startswith(("use.", "preview.")):
            return "generation"
        if suffix.startswith(("apply.", "extract.", "save.", "combine.", "group.", "mask_refine.")):
            return "apply"
        return "other"

    def _make_menu(self, group: str, widgets: list[QWidget]) -> QToolButton:
        button = QToolButton(self)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMinimumHeight(20)
        button.setObjectName(f"sam_select_menu_{group}")
        menu = QMenu(button)
        button.setMenu(menu)
        scroll = QScrollArea(menu)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        for widget in widgets:
            layout.addWidget(widget)
            if isinstance(widget, QPushButton):
                widget.clicked.connect(menu.hide)
        scroll.setWidget(content)
        action = QWidgetAction(menu)
        action.setDefaultWidget(scroll)
        menu.addAction(action)
        self.menu_buttons[group] = button
        self.menu_widgets[group] = widgets
        self._scrolls[group] = scroll
        menu.aboutToShow.connect(lambda key=group: self._fit_menu(key))
        self._fit_menu(group)
        return button

    def _fit_menu(self, group: str) -> None:
        scroll = self._scrolls[group]
        screen = self.menu_buttons[group].screen().availableGeometry()
        scroll.setFixedSize(
            min(340, max(160, screen.width() - 40)),
            min(len(self.menu_widgets[group]) * 28 + 16, 400, screen.height() - 40),
        )

    def sync_states(self) -> None:
        for group, button in self.menu_buttons.items():
            # A disabled menu trigger also disables its descendants. Inspect
            # each control up to the menu so a later snapshot can re-enable it.
            button.setEnabled(
                any(widget.isEnabledTo(button.menu()) for widget in self.menu_widgets[group])
            )
        operation = self.menu_buttons.get("operation")
        if operation is not None:
            selected = next(
                (
                    widget
                    for widget in self.menu_widgets["operation"]
                    if isinstance(widget, QPushButton) and widget.isChecked()
                ),
                None,
            )
            operation.setText(selected.text() if selected is not None else self._text("Operation"))
        summary = self.widgets.get(_PREFIX + "candidate.summary")
        if isinstance(summary, QLabel):
            summary.setToolTip(summary.text())

    @staticmethod
    def _text(source: str) -> str:
        return translated_literal_domain("ToolPropertiesPanel", source, use_fallback=True)

    def retranslate(self) -> None:
        for group, button in self.menu_buttons.items():
            text = self._text(_TITLES[group])
            button.setText(text)
            button.setAccessibleName(text)
            button.setToolTip(text)
        for key, spec in self.specs.items():
            widget = self.widgets[key]
            if not isinstance(widget, QPushButton):
                continue
            source = getattr(spec.label, "source_text", None)
            domain = getattr(spec.label, "translation_domain", None)
            if source and domain:
                widget.setText(translated_literal_domain(domain, source, use_fallback=True))
        self.sync_states()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()

    def hideEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        for button in self.menu_buttons.values():
            button.menu().hide()
        super().hideEvent(event)
