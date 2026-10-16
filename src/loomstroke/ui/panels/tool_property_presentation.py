"""Group existing tool property controls without altering SDK metadata or values."""

from functools import partial
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from loomstroke.ui.tool_settings_sections import sections_for_tool
from loomstroke.ui.translations import translated_literal_domain
from loomstroke.ui.widgets.settings_section import SettingsSection


class ToolPropertyPresentation(QObject):
    """Own session-only disclosure state; ToolPropertiesPanel owns the bindings."""

    def __init__(self, owner: Any) -> None:
        super().__init__(owner)
        self.owner = owner
        self.sections: dict[str, SettingsSection] = {}
        self._expanded: dict[tuple[str, str], bool] = {}
        self._tool_id = "basic_pen"
        self._groups: tuple = ()
        self._domain = "BasicPen"
        self._prefix = "pen"
        self.detail_button: QToolButton | None = None
        self._detail_menu: QMenu | None = None
        self._detail_scroll: QScrollArea | None = None

    def configure(self, metadata: Any) -> bool:
        self._tool_id = metadata.tool_id
        self._groups = sections_for_tool(metadata)
        self._domain = "BasicPen" if self._tool_id == "basic_pen" else "ToolPropertiesPanel"
        self._prefix = "pen" if self._tool_id == "basic_pen" else "tool"
        return bool(self._groups)

    def clear(self) -> None:
        self._expanded.update(
            {(self._tool_id, key): section.is_expanded for key, section in self.sections.items()}
        )
        if self._detail_menu is not None:
            self._detail_menu.hide()
        self.sections.clear()
        self.detail_button = None
        self._detail_menu = None
        self._detail_scroll = None

    def _remember(self, key: str, expanded: bool) -> None:
        self._expanded[self._tool_id, key] = expanded
        self._fit_popup()
        QTimer.singleShot(0, self, self._fit_popup)

    @property
    def popup_visible(self) -> bool:
        return self._detail_menu is not None and self._detail_menu.isVisible()

    def _fit_popup(self) -> None:
        if self._detail_menu is None or self._detail_scroll is None:
            return
        menu, scroll = self._detail_menu, self._detail_scroll
        scroll.widget().layout().invalidate()
        scroll.widget().layout().activate()
        bounds = menu.screen().availableGeometry()
        scroll.setFixedHeight(
            min(max(160, scroll.widget().sizeHint().height() + 8), 480, bounds.height() - 40)
        )
        # QMenu caches QWidgetAction geometry until the action changes.
        menu.actions()[0].setData((scroll.width(), scroll.height()))
        if menu.isVisible():
            menu.adjustSize()
            menu.move(
                max(bounds.left(), min(menu.x(), bounds.right() - menu.width() + 1)),
                max(bounds.top(), min(menu.y(), bounds.bottom() - menu.height() + 1)),
            )

    def _populate(self, parent: QWidget, items: dict[str, QWidget]) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        groups = (*self._groups, ("other", "Other Settings", tuple(items)))
        remaining = dict(items)
        for key, title, keys in groups:
            selected = [remaining.pop(field) for field in keys if field in remaining]
            if not selected:
                continue
            section = SettingsSection(
                title,
                parent,
                expanded=self._expanded.get((self._tool_id, key), key == "basic"),
                domain=self._domain,
            )
            section.setObjectName(f"{self._prefix}_section_{key}")
            section.expandedChanged.connect(partial(self._remember, key))
            self.sections[key] = section
            form = QFormLayout()
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            section.content_layout.addLayout(form)
            for item in selected:
                row = item.layout()
                label, control = row.takeAt(0).widget(), row.takeAt(0).widget()
                if isinstance(label, QLabel):
                    label.setWordWrap(True)
                    label.setBuddy(control)
                if isinstance(control, QCheckBox):
                    control.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                form.addRow(label, control)
                item.deleteLater()
            layout.addWidget(section)
        layout.addStretch(1)

    def _items_by_key(self, properties: list[Any], items: list[QWidget]) -> dict[str, QWidget]:
        return {spec.key: item for spec, item in zip(properties, items, strict=True)}

    def populate_panel(self, properties: list[Any], settings: dict[str, Any]) -> bool:
        owner = self.owner
        plugin = owner._tool_plugin(self._tool_id)
        custom = None
        if getattr(plugin, "options_widget_in_properties", False):
            custom = plugin.create_options_widget()
        if custom is not None:
            custom_keys = set(getattr(plugin, "options_widget_property_keys", ()))
            properties = [spec for spec in properties if spec.key not in custom_keys]
        items = owner._build_bar_property_items(properties, settings)
        content = QWidget(owner.specific_group)
        self._populate(content, self._items_by_key(properties, items))
        owner.specific_form.addWidget(content, 0, 0)
        content.show()
        if custom is not None:
            owner.specific_form.addWidget(custom, 1, 0)
            custom.show()
        return bool(items) or custom is not None

    def arrange_bar(self, properties: list[Any], items: list[QWidget]) -> list[QWidget]:
        by_key = self._items_by_key(properties, items)
        basic_keys = next((keys for key, _title, keys in self._groups if key == "basic"), ())
        primary = [by_key.pop(spec.key) for spec in properties if spec.key in basic_keys]
        if not by_key:
            return primary
        button = QToolButton(self.owner)
        source = "Pen Settings" if self._tool_id == "basic_pen" else "Tool Settings"
        text = translated_literal_domain(self._domain, source, use_fallback=True)
        button.setText(text)
        button.setToolTip(text)
        button.setAccessibleName(text)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.owner._apply_compact_tool_button(button)
        menu = QMenu(button)
        button.setMenu(menu)
        scroll = QScrollArea(menu)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        screen = button.screen().availableGeometry()
        scroll.setFixedSize(
            min(360, max(160, screen.width() - 40)), min(480, max(160, screen.height() - 80))
        )
        content = QWidget(scroll)
        self._populate(content, by_key)
        scroll.setWidget(content)
        action = QWidgetAction(menu)
        action.setDefaultWidget(scroll)
        menu.addAction(action)
        self.detail_button, self._detail_menu, self._detail_scroll = button, menu, scroll
        menu.aboutToShow.connect(self._fit_popup)
        self._fit_popup()
        return [*primary, button]
