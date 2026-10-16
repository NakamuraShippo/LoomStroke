"""Floating context toolbar widget.

Renders as a compact two-row bar positioned above the anchor rectangle.
This is a pure view — all business logic lives in ContextToolbarManager.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.plugins.sdk.tool import PropertySpec
from loomstroke.ui.context_toolbar.sam_select_presentation import SamSelectToolbarPresentation
from loomstroke.ui.widgets.property_widget_factory import (
    create_context_widget,
    create_widget_for_spec,
)

if TYPE_CHECKING:
    from loomstroke.plugins.sdk.tool import ContextToolbarSpec

logger = logging.getLogger(__name__)

# Minimum property count to split into two rows
_TWO_ROW_THRESHOLD = 3


class ContextToolbar(QWidget):
    """Floating toolbar rendered on top of the RulerCanvasFrame."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setVisible(False)

        # Styling
        self.setStyleSheet(
            "ContextToolbar {"
            "  background-color: palette(window);"
            "  border: 1px solid palette(mid);"
            "  border-radius: 4px;"
            "  padding: 2px 4px;"
            "}"
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(8)
        shadow.setOffset(0, 2)
        shadow.setColor(Qt.GlobalColor.black)
        self.setGraphicsEffect(shadow)

        # Root layout — vertical (holds one or two rows)
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(4, 2, 4, 2)
        self._root_layout.setSpacing(2)

        # Row layouts (created on demand in build_from_spec)
        self._row_widgets: list[QWidget] = []

        # Property bindings: key -> (widget, setter, getter)
        self._bindings: dict[str, tuple[QWidget, Callable[[Any], None], Callable[[], Any]]] = {}
        # Custom action widgets: action key -> widget
        self._action_widgets: dict[str, QWidget] = {}
        # Custom content widget (when build_widget_fn is used)
        self._custom_content: QWidget | None = None

    def build_from_spec(
        self,
        spec: ContextToolbarSpec,
        property_specs: list[PropertySpec],
        on_changed: Callable[[str, Any], None],
        on_action: Callable[[str], None] | None = None,
        translate_fn: Callable[[str], str] | None = None,
    ) -> None:
        """Populate the toolbar from a ContextToolbarSpec + PropertySpec list."""
        self._clear()

        # ---- Custom widget builder path ----
        if spec.build_widget_fn is not None:
            try:
                custom = spec.build_widget_fn(self, on_changed)
            except Exception:
                logger.exception("build_widget_fn failed")
                custom = None
            if custom is not None:
                self._custom_content = custom
                self._root_layout.addWidget(custom)
                self.adjustSize()
                return

        if not property_specs and SamSelectToolbarPresentation.accepts(spec.custom_widgets or ()):
            self._custom_content = SamSelectToolbarPresentation(
                self,
                spec.custom_widgets or (),
                lambda widget_spec: self._create_custom_widget(widget_spec, on_action),
            )
            self._root_layout.addWidget(self._custom_content)
            self.adjustSize()
            return

        # ---- Generic PropertySpec path ----
        total = len(property_specs)
        use_two_rows = total >= _TWO_ROW_THRESHOLD

        if use_two_rows:
            split = math.ceil(total / 2)
            row1_specs = property_specs[:split]
            row2_specs = property_specs[split:]
        else:
            row1_specs = property_specs
            row2_specs = []

        # Build rows
        self._build_row(row1_specs, on_changed, translate_fn)
        if row2_specs:
            self._build_row(row2_specs, on_changed, translate_fn)

        if spec.custom_widgets:
            self._place_custom_widgets(spec.custom_widgets, on_action)

        self.adjustSize()

    def _place_custom_widgets(
        self,
        custom_widgets: Iterable[Any],
        on_action: Callable[[str], None] | None,
    ) -> None:
        """Place custom widgets: unrowed specs share the last row (legacy
        layout), specs with an explicit row index get one row per index."""
        unrowed: list[Any] = []
        rowed: dict[int, list[Any]] = {}
        for cw_spec in custom_widgets:
            row_index = getattr(cw_spec, "row", None)
            if row_index is None:
                unrowed.append(cw_spec)
            else:
                rowed.setdefault(int(row_index), []).append(cw_spec)

        if unrowed:
            last_row_layout = self._row_widgets[-1].layout() if self._row_widgets else None
            if last_row_layout is None:
                last_row_layout = self._new_custom_row()
            for cw_spec in unrowed:
                last_row_layout.addWidget(self._create_custom_widget(cw_spec, on_action))

        for row_index in sorted(rowed):
            row_layout = self._new_custom_row()
            for cw_spec in rowed[row_index]:
                row_layout.addWidget(self._create_custom_widget(cw_spec, on_action))
            row_layout.addStretch(1)

    def _new_custom_row(self) -> QHBoxLayout:
        row_widget = QWidget(self)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        self._root_layout.addWidget(row_widget)
        self._row_widgets.append(row_widget)
        return row_layout

    def _create_custom_widget(
        self,
        spec: Any,
        on_action: Callable[[str], None] | None,
    ) -> QWidget:
        if spec.kind == "button":
            button = QPushButton(str(spec.label), self)
            button.setMinimumHeight(20)
            button.setMaximumHeight(24)
            button.setStyleSheet(
                "QPushButton { padding: 2px 6px; }"
                "QPushButton:checked {"
                "  background-color: palette(highlight);"
                "  color: palette(highlighted-text);"
                "  border: 1px solid palette(highlight);"
                "}"
            )
            if isinstance(spec.key, str) and spec.key:
                self._action_widgets[spec.key] = button
            if spec.on_clicked:
                button.clicked.connect(spec.on_clicked)
            elif on_action is not None:
                button.clicked.connect(lambda _checked=False, key=spec.key: on_action(key))
            return button
        widget = create_context_widget(spec, parent=self)
        if isinstance(spec.key, str) and spec.key:
            self._action_widgets[spec.key] = widget
        return widget

    def apply_action_states(self, actions: Iterable[Mapping[str, Any]]) -> None:
        """Apply state from a context action snapshot to custom buttons."""
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            action_id = action.get("id")
            if not isinstance(action_id, str):
                continue
            widget = self._action_widgets.get(action_id)
            if widget is None:
                continue
            if "enabled" in action:
                widget.setEnabled(bool(action.get("enabled")))
            if "text" in action and isinstance(widget, QLabel):
                text = action.get("text")
                widget.setText("" if text is None else str(text))
            if "checked" in action and isinstance(widget, QPushButton):
                self._apply_button_checked_state(widget, action)
        if isinstance(self._custom_content, SamSelectToolbarPresentation):
            self._custom_content.sync_states()

    @staticmethod
    def _apply_button_checked_state(button: QPushButton, action: Mapping[str, Any]) -> None:
        checked = bool(action.get("checked"))
        if checked or action.get("kind") == "operation" or button.isCheckable():
            button.setCheckable(True)
            button.setChecked(checked)

    def _build_row(
        self,
        specs: list[PropertySpec],
        on_changed: Callable[[str, Any], None],
        translate_fn: Callable[[str], str] | None,
    ) -> None:
        """Create a single horizontal row of property widgets."""
        row_widget = QWidget(self)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        for ps in specs:
            label = QLabel(ps.display_name, self)
            label.setStyleSheet("font-size: 11px;")
            row_layout.addWidget(label)

            widget, setter, getter = create_widget_for_spec(
                ps,
                parent=self,
                on_changed=on_changed,
                compact=True,
                translate_fn=translate_fn,
            )
            row_layout.addWidget(widget)
            self._bindings[ps.key] = (widget, setter, getter)

        row_layout.addStretch(1)
        self._root_layout.addWidget(row_widget)
        self._row_widgets.append(row_widget)

    def set_value(self, key: str, value: Any) -> None:
        """Programmatically update a property widget value."""
        # Delegate to custom content if present
        if self._custom_content is not None:
            fn = getattr(self._custom_content, "set_value", None)
            if callable(fn):
                fn(key, value)
            return
        binding = self._bindings.get(key)
        if binding:
            _, setter, _ = binding
            setter(value)

    def get_value(self, key: str) -> Any:
        """Read current value from a property widget."""
        binding = self._bindings.get(key)
        if binding:
            _, _, getter = binding
            return getter()
        return None

    def _clear(self) -> None:
        """Remove all child widgets."""
        self._bindings.clear()
        self._action_widgets.clear()
        if self._custom_content is not None:
            self._custom_content.hide()
            self._custom_content.deleteLater()
            self._custom_content = None
        for row_widget in self._row_widgets:
            row_layout = row_widget.layout()
            if row_layout is not None:
                while row_layout.count():
                    item = row_layout.takeAt(0)
                    if item is None:
                        continue
                    w = item.widget()
                    if w is not None:
                        w.hide()
                        w.deleteLater()
            row_widget.hide()
            row_widget.deleteLater()
        self._row_widgets.clear()
