"""Named pen presets for the Brush Manager pen tab, grouped by what they paint with."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QEvent, QRectF, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.translations import TranslationBinder, translated_literal_domain
from loomstroke.ui.widgets.responsive_button_grid import ResponsiveButtonGrid

from .pen_preset import MAX_NAME_LENGTH, PenPreset
from .pen_preset_list_widget import PenPresetListWidget as _PresetListWidget
from .pen_round_tip import is_round_tip

_ICON_SIZE = 48
_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
    "{shape}</svg>"
)
# Category id, the medium a preset of it stores, its label and its icon.
PEN_CATEGORIES = (
    (
        "basic",
        None,
        "Basic",
        '<path d="m4 20 1.2-5L16.4 3.8a2 2 0 0 1 2.8 0l1 1a2 2 0 0 1 0 2.8L9 18.8z"/>'
        '<path d="m14.4 5.8 3.8 3.8"/>',
    ),
    (
        "watercolor",
        "Watercolor",
        "Watercolor",
        '<path d="M12 3c3.3 4.1 6 7.3 6 10.7a6 6 0 0 1-12 0C6 10.3 8.7 7.1 12 3z"/>'
        '<path d="M9.3 14.6a3 3 0 0 0 2.3 2.5"/>',
    ),
    (
        "oil",
        "Oil",
        "Oil",
        '<path d="m13 12 6.8-8.2c1.2-1.4 3 .1 1.8 1.6L15 14.7z"/>'
        '<path d="M13 12c-3.3-.8-5.8 1.7-5.7 4.4 0 1.4-1 2.8-3.4 3.3 '
        '3.2 1.7 8.6 1 10.6-2 1-1.5 1.2-2.4.5-3"/>',
    ),
)


def _ts(source: str) -> str:
    return translated_literal_domain("PenPresets", source, use_fallback=True)


def pen_category(preset: PenPreset) -> str:
    """The group a preset is listed in: the medium it stores, or the basic pen."""
    medium = preset.medium["medium"] if preset.medium else None
    return next(key for key, stored, _label, _shape in PEN_CATEGORIES if stored == medium)


def _category_icon(shape: str, color: QColor) -> QIcon:
    renderer = QSvgRenderer(QByteArray(_SVG.format(color=color.name(), shape=shape).encode()))
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def _tip_icon(tip_png: bytes | None, outline: QColor) -> QIcon:
    """The preset's brush shape: its tip image as it is, like the Brushes tab thumbnails.

    Only the plain round tip gets an outline, so that its white disc shows on a light theme.
    """
    image = QImage()
    if not tip_png or not image.loadFromData(tip_png, "PNG"):
        return QIcon()
    pixmap = QPixmap.fromImage(
        image.scaled(
            _ICON_SIZE,
            _ICON_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    )
    if is_round_tip(image):
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(outline, 1.5))
        painter.drawEllipse(QRectF(1.0, 1.0, _ICON_SIZE - 2.0, _ICON_SIZE - 2.0))
        painter.end()
    return QIcon(pixmap)


class PenPresetPanel(QWidget):
    """List the saved pens by category and expose requests; file I/O stays outside."""

    presetSelected = Signal(str)
    requestSave = Signal()
    requestUpdate = Signal()
    requestImport = Signal()
    requestExport = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._translations = TranslationBinder()
        self._library: list[PenPreset] = []
        self._category = PEN_CATEGORIES[0][0]
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self._create_categories(layout)
        self._create_list(layout)
        self._create_actions(layout)
        self.name_edit.textChanged.connect(self._update_buttons)
        self._paint_category_icons()
        self._update_buttons()

    def _create_categories(self, layout: QVBoxLayout) -> None:
        self.category_buttons: dict[str, QToolButton] = {}
        self._category_group = QButtonGroup(self)
        self._category_group.setExclusive(True)
        categories = QHBoxLayout()
        categories.setSpacing(4)
        for key, _medium, label, _shape in PEN_CATEGORIES:
            button = QToolButton(self)
            button.setObjectName(f"pen_category_{key}")
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setIconSize(QSize(24, 24))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            for apply in (button.setText, button.setToolTip, button.setAccessibleName):
                self._translations.register(apply, _ts(label))
            button.setChecked(key == self._category)
            button.clicked.connect(lambda _checked=False, key=key: self.set_category(key))
            self._category_group.addButton(button)
            self.category_buttons[key] = button
            categories.addWidget(button)
        layout.addLayout(categories)

    def _create_list(self, layout: QVBoxLayout) -> None:
        self.presets = _PresetListWidget(self)
        self.presets.setViewMode(QListWidget.ViewMode.IconMode)
        self.presets.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.presets.setMovement(QListWidget.Movement.Static)
        self.presets.setWordWrap(True)
        self.presets.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        self.presets.setGridSize(QSize(_ICON_SIZE + 28, _ICON_SIZE + 40))
        self.presets.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.presets.setMinimumHeight(80)
        self._translations.register(self.presets.setAccessibleName, _ts("Pen Presets"))
        self.presets.currentItemChanged.connect(self._preset_selected)
        self.presets.reselected.connect(self._preset_reselected)
        layout.addWidget(self.presets, 1)

    def _create_actions(self, layout: QVBoxLayout) -> None:
        self.name_label = QLabel(self)
        self.name_edit = QLineEdit(self)
        self.name_edit.setMaxLength(MAX_NAME_LENGTH)
        self.name_label.setBuddy(self.name_edit)
        self._translations.register(self.name_label.setText, _ts("Preset Name"))
        self._translations.register(self.name_edit.setAccessibleName, _ts("Preset Name"))
        layout.addWidget(self.name_label)
        layout.addWidget(self.name_edit)

        buttons = []
        self.save_button = QPushButton(self)
        self.update_button = QPushButton(self)
        self.import_button = QPushButton(self)
        self.export_button = QPushButton(self)
        for button, source, signal in (
            (self.save_button, "Save New", self.requestSave),
            (self.update_button, "Update", self.requestUpdate),
            (self.import_button, "Import", self.requestImport),
            (self.export_button, "Export", self.requestExport),
        ):
            self._translations.register(button.setText, _ts(source))
            button.clicked.connect(signal.emit)
            buttons.append(button)
        self.preset_actions = ResponsiveButtonGrid(buttons, self)
        layout.addWidget(self.preset_actions)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(100, 50)

    @property
    def category(self) -> str:
        return self._category

    def set_category(self, key: str) -> None:
        """Show another category; the pen in use changes only when a preset is chosen."""
        if key not in self.category_buttons:
            raise ValueError("Unknown pen category")
        selected = self.selected_preset_id()
        self._category = key
        self.category_buttons[key].setChecked(True)
        self._fill(selected)

    def _paint_category_icons(self) -> None:
        color = self.palette().buttonText().color()
        for key, _medium, _label, shape in PEN_CATEGORIES:
            self.category_buttons[key].setIcon(_category_icon(shape, color))

    def _fill(self, selected_id: str | None) -> None:
        outline = self.palette().text().color()
        with QSignalBlocker(self.presets):
            self.presets.clear()
            for preset in self._library:
                if pen_category(preset) != self._category:
                    continue
                item = QListWidgetItem(_tip_icon(preset.tip_png, outline), preset.name)
                item.setData(Qt.ItemDataRole.UserRole, preset.id)
                item.setToolTip(preset.name)
                item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
                self.presets.addItem(item)
                if preset.id == selected_id:
                    self.presets.setCurrentItem(item)
        self._update_buttons()

    def set_presets(self, presets: list[PenPreset], selected_id: str | None = None) -> None:
        """Refresh the library while retaining the user's pending preset name.

        A preset named by selected_id is shown selected, in whichever category it is in.
        """
        self._library = list(presets)
        if selected_id is None:
            selected_id = self.selected_preset_id()
        else:
            self._show_category_of(selected_id)
        self._fill(selected_id)

    def _show_category_of(self, preset_id: str | None) -> None:
        preset = next((p for p in self._library if p.id == preset_id), None)
        if preset is not None:
            self._category = pen_category(preset)
            self.category_buttons[self._category].setChecked(True)

    def selected_preset_id(self) -> str | None:
        item = self.presets.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def preset_name(self) -> str:
        return self.name_edit.text().strip()

    def set_selected_preset(self, preset_id: str | None) -> None:
        """Select a preset programmatically and display its stored name."""
        self._show_category_of(preset_id)
        self._fill(preset_id)
        item = self.presets.currentItem()
        if item is not None:
            self.name_edit.setText(item.text())

    def _preset_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        self._update_buttons()
        if current is not None:
            self.name_edit.setText(current.text())
            self.presetSelected.emit(current.data(Qt.ItemDataRole.UserRole))

    def _preset_reselected(self, current: QListWidgetItem) -> None:
        self._preset_selected(current, None)

    def _update_buttons(self, _text: str = "") -> None:
        named = bool(self.preset_name())
        selected = self.selected_preset_id() is not None
        self.save_button.setEnabled(named)
        self.update_button.setEnabled(named and selected)
        self.export_button.setEnabled(selected)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._translations.retranslate()
        elif event.type() == QEvent.Type.PaletteChange and getattr(self, "preset_actions", None):
            self._paint_category_icons()
            self._fill(self.selected_preset_id())
