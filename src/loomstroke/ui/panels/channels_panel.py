"""Channel rows and actions; pixel editing remains in the canvas service."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QItemSelectionModel, QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.translations import TranslationBinder, translate_with_fallback
from loomstroke.ui.widgets.native_cmyk_foreground_controls import NativeCmykForegroundControls

from .channels_panel_controller import ChannelsPanelController


class ChannelsPanel(QWidget):
    def __init__(
        self, host: Any = None, event_bus: Any = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._signature: Any = None
        self._refreshing = False
        self._translations = TranslationBinder(self._ts)
        self.controller = ChannelsPanelController(self, host, event_bus)
        self.destroyed.connect(self.controller.dispose)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.description = QLabel(self)
        self.description.setWordWrap(True)
        self._translations.register(
            self.description.setText,
            "Channels of the active layer; alpha channels store selections.",
        )
        layout.addWidget(self.description)
        self.ink_controls = NativeCmykForegroundControls(
            lambda: self.controller.service, self, host_provider=lambda: self.controller.host
        )
        layout.addWidget(self.ink_controls)
        self.rows = QListWidget(self)
        self.rows.setIconSize(QSize(42, 32))
        self.rows.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.rows.itemSelectionChanged.connect(self._select)
        layout.addWidget(self.rows)
        self.show_channel = QCheckBox(self)
        self.show_channel.setChecked(True)
        self._translations.register(self.show_channel.setText, "Show selected channel on canvas")
        self.show_channel.toggled.connect(self._view)
        layout.addWidget(self.show_channel)
        self.operation = QComboBox(self)
        for key in ("replace", "add", "subtract", "intersect"):
            self.operation.addItem("", key)
        for i, source in enumerate(
            (
                "Replace Selection",
                "Add to Selection",
                "Subtract from Selection",
                "Intersect Selection",
            )
        ):
            self._translations.register(
                lambda text, index=i: self.operation.setItemText(index, text), source
            )
        layout.addWidget(self.operation)
        self.buttons: dict[str, QPushButton] = {}
        for names in (
            ("New Alpha", "Save Selection", "Load Selection"),
            ("Duplicate", "Rename", "Delete", "Invert"),
        ):
            row = QHBoxLayout()
            for name in names:
                button = QPushButton(self)
                button.setProperty("sourceText", name)
                self._translations.register(button.setText, name)
                self.buttons[name] = button
                row.addWidget(button)
            layout.addLayout(row)
        self.buttons["New Alpha"].clicked.connect(lambda: self._create(False))
        self.buttons["Save Selection"].clicked.connect(lambda: self._create(True))
        self.buttons["Load Selection"].clicked.connect(self._load)
        self.buttons["Duplicate"].clicked.connect(lambda: self._alpha_action("duplicate_alpha"))
        self.buttons["Delete"].clicked.connect(lambda: self._alpha_action("delete_alpha"))
        self.buttons["Rename"].clicked.connect(self._rename)
        self.buttons["Invert"].clicked.connect(lambda: self.controller.run(lambda s: s.invert()))
        self.error = QLabel(self)
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        self.refresh()

    @staticmethod
    def _ts(text: str) -> str:
        return translate_with_fallback("ChannelsPanel", text)

    def set_host_api(self, host: Any) -> None:
        self.controller.set_host(host)
        self._signature = None
        self.refresh()

    def refresh(self) -> None:
        if not hasattr(self, "rows"):
            return
        service = self.controller.service
        self._refreshing = True
        try:
            self._translations.retranslate()
            self.ink_controls.refresh()
            for button in self.buttons.values():
                button.setEnabled(service is not None)
            if service is None:
                self.rows.clear()
                return
            layer = service._canvas._get_active_layer()
            alphas = service.alpha_layers()
            signature = (
                getattr(layer, "id", None),
                getattr(layer, "texture_version", 0),
                service.selected,
                service.view,
                tuple((a.id, a.name, a.texture_version) for a in alphas),
            )
            if signature == self._signature:
                return
            self._signature = signature
            current = self.rows.currentItem()
            current_key = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
            self.rows.clear()
            keys = service.color_keys
            names = (
                ("Cyan", "Magenta", "Yellow", "Black")
                if len(keys) == 4
                else ("Red", "Green", "Blue")
            )
            composite = "cmyk" if len(keys) == 4 else "rgb"
            entries = [
                (composite, composite.upper()),
                *zip(keys, names, strict=True),
                ("transparency", "Transparency (read only)"),
            ]
            entries.extend((alpha.id, alpha.name) for alpha in alphas)
            alpha_ids = {a.id for a in alphas}
            for key, name in entries:
                item = QListWidgetItem(name if key in alpha_ids else self._ts(name))
                item.setData(Qt.ItemDataRole.UserRole, key)
                if key in keys and getattr(layer, "layer_type", None) not in (
                    "raster",
                    "mask",
                ):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                preview = service.channel_image(key, max_extent=48)
                if not preview.isNull():
                    item.setIcon(QIcon(QPixmap.fromImage(preview)))
                self.rows.addItem(item)
                selected = (
                    layer.id == key
                    if key in alpha_ids
                    else key == composite
                    and service.selected == service.all_colors
                    and not service.is_alpha(layer)
                    and service.view != (layer.id, "transparency")
                )
                if key == "transparency" and not service.is_alpha(layer):
                    selected = service.view == (layer.id, "transparency")
                if key in keys and not service.is_alpha(layer):
                    selected = (
                        keys.index(key) in service.selected
                        and service.selected != service.all_colors
                    )
                item.setSelected(selected)
                if key == current_key:
                    self.rows.setCurrentItem(item, QItemSelectionModel.SelectionFlag.NoUpdate)
            self.error.hide()
        finally:
            self._refreshing = False

    def _keys(self) -> list[str]:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.rows.selectedItems()]

    def _select(self) -> None:
        if self._refreshing:
            return
        keys = self._keys()
        if not keys:
            return

        def action(service: Any) -> None:
            alpha_ids = {layer.id for layer in service.alpha_layers()}
            colors = service.color_keys
            composite = "cmyk" if len(colors) == 4 else "rgb"
            last = keys[-1]
            if last in alpha_ids:
                service.select_alpha(last)
            elif last == "transparency":
                service.select_color(service.all_colors)
                service.set_view("transparency")
            elif composite in keys and not any(key in keys for key in colors):
                service.select_color(service.all_colors)
            else:
                components = tuple(i for i, key in enumerate(colors) if key in keys)
                if components:
                    service.select_color(components)
            if not self.show_channel.isChecked():
                service.set_view(None)

        self.controller.run(action, refresh=False)

    def _view(self, enabled: bool) -> None:
        if enabled:
            self._select()
        else:
            self.controller.run(lambda s: s.set_view(None))

    def _create(self, from_selection: bool) -> None:
        name, accepted = QInputDialog.getText(
            self, self._ts("New Alpha"), self._ts("Name"), text=self._ts("Alpha")
        )
        if accepted and name.strip():
            self.controller.run(lambda s: s.create_alpha(name, from_selection=from_selection))

    def _alpha_action(self, method: str) -> None:
        key = self._alpha_key()
        if key is not None:
            self.controller.run(lambda s: getattr(s, method)(key))

    def _alpha_key(self) -> str | None:
        service = self.controller.service
        if service is None:
            return None
        identifiers = {a.id for a in service.alpha_layers()}
        keys = [key for key in self._keys() if key in identifiers]
        return keys[0] if len(keys) == 1 else None

    def _load(self) -> None:
        key = self._alpha_key()
        if key is not None:
            self.controller.run(lambda s: s.load_selection(key, self.operation.currentData()))

    def _rename(self) -> None:
        key = self._alpha_key()
        if key is None:
            return
        service = self.controller.service
        alpha = next((a for a in service.alpha_layers() if a.id == key), None) if service else None
        if alpha is None:
            return
        name, accepted = QInputDialog.getText(
            self, self._ts("Rename"), self._ts("Name"), text=alpha.name
        )
        if accepted and name.strip():
            self.controller.run(lambda s: s.rename_alpha(alpha.id, name))

    def show_error(self, source: str) -> None:
        self.error.setText(self._ts(source))
        self.error.show()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._signature = None
            self.refresh()
