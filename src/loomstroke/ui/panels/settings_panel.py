"""
Settings panel for application preferences.

Manages canvas, performance, and UI settings.
Per Master.md, this panel is under 400 lines.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QEvent, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.typing import Signal0, Signal2

from ...core.event_bus_extensions import get_extended_event_bus
from ...core.events import Events
from ...platform import SettingsManager
from ...platform.user_dirs import default_layout_directory
from ..translations import TranslationBinder, translate_with_fallback, translated_literal
from ..widgets.spinbox_sizing import apply_dimension_spinbox_policy, fit_spinbox_to_values


class SettingsPanel(QWidget):
    """
    Panel for application settings.

    Replaces settings management from comfy_panel.py.
    """

    # Signals
    settings_changed = Signal(str, object)  # key, value
    reset_requested = Signal()

    def __init__(self, settings_manager: SettingsManager, parent: QWidget | None = None):
        """
        Initialize settings panel.

        Args:
            settings_manager: SettingsManager instance
            parent: Parent widget
        """
        super().__init__(parent)

        self.settings_manager: SettingsManager = settings_manager
        self._event_bus = get_extended_event_bus()
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._event_handlers: list[Any] = []
        self._event_tokens: list[Any] = []
        self._setup_ui()
        self._load_settings()
        self._connect_signals()
        self._subscribe_events()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        self._setup_canvas_settings_group(layout)
        self._setup_performance_settings_group(layout)
        self._setup_ui_settings_group(layout)
        self._setup_io_settings_group(layout)
        self._setup_control_buttons(layout)
        layout.addStretch()

    def _setup_canvas_settings_group(self, layout: QVBoxLayout) -> None:
        # Canvas settings
        canvas_group = QGroupBox()
        canvas_title = self._ts("Canvas Settings")
        self._translation_binder.register(canvas_group.setTitle, canvas_title)
        canvas_layout = QFormLayout()

        # Canvas size
        size_layout = QHBoxLayout()
        self.width_spin = QSpinBox()
        self.width_spin.setRange(100, 8192)
        width_suffix = self._ts(" px")
        self.width_spin.setSuffix(width_suffix)
        self._translation_binder.register(self.width_spin.setSuffix, width_suffix)
        apply_dimension_spinbox_policy(self.width_spin, (100, 8192))
        size_layout.addWidget(self.width_spin)

        size_layout.addWidget(QLabel("x"))

        self.height_spin = QSpinBox()
        self.height_spin.setRange(100, 8192)
        height_suffix = self._ts(" px")
        self.height_spin.setSuffix(height_suffix)
        self._translation_binder.register(self.height_spin.setSuffix, height_suffix)
        apply_dimension_spinbox_policy(self.height_spin, (100, 8192))
        size_layout.addWidget(self.height_spin)

        self._add_labeled_row(canvas_layout, self._ts("Size:"), size_layout)

        # DPI
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 600)
        dpi_suffix = self._ts(" dpi")
        self.dpi_spin.setSuffix(dpi_suffix)
        self._translation_binder.register(self.dpi_spin.setSuffix, dpi_suffix)
        fit_spinbox_to_values(self.dpi_spin, (72, 600))
        self._add_labeled_row(canvas_layout, self._ts("DPI:"), self.dpi_spin)

        # Tile size
        self.tile_size_combo = QComboBox()
        self.tile_size_combo.addItems(["128", "256", "512", "1024"])
        self._add_labeled_row(canvas_layout, self._ts("Tile Size:"), self.tile_size_combo)

        # Max layers
        self.max_layers_spin = QSpinBox()
        self.max_layers_spin.setRange(1, 500)
        self._add_labeled_row(canvas_layout, self._ts("Max Layers:"), self.max_layers_spin)

        canvas_group.setLayout(canvas_layout)
        layout.addWidget(canvas_group)

    def _setup_performance_settings_group(self, layout: QVBoxLayout) -> None:
        # Performance settings
        perf_group = QGroupBox()
        perf_title = self._ts("Performance Settings")
        self._translation_binder.register(perf_group.setTitle, perf_title)
        perf_layout = QFormLayout()

        # Numba JIT
        numba_text = self._ts("Enable Numba JIT")
        self.use_numba_check = QCheckBox(numba_text)
        self._translation_binder.register(self.use_numba_check.setText, numba_text)
        perf_layout.addRow(self.use_numba_check)

        # Cache size
        self.cache_size_spin = QSpinBox()
        self.cache_size_spin.setRange(10, 1000)
        cache_suffix = self._ts(" items")
        self.cache_size_spin.setSuffix(cache_suffix)
        self._translation_binder.register(self.cache_size_spin.setSuffix, cache_suffix)
        self._add_labeled_row(perf_layout, self._ts("Cache Size:"), self.cache_size_spin)

        # Undo steps
        self.undo_steps_spin = QSpinBox()
        self.undo_steps_spin.setRange(10, 200)
        self._add_labeled_row(perf_layout, self._ts("Undo Steps:"), self.undo_steps_spin)

        # Memory threshold
        self.memmap_threshold_spin = QSpinBox()
        self.memmap_threshold_spin.setRange(1, 100)
        memmap_suffix = self._ts(" MB")
        self.memmap_threshold_spin.setSuffix(memmap_suffix)
        self._translation_binder.register(self.memmap_threshold_spin.setSuffix, memmap_suffix)
        self._add_labeled_row(
            perf_layout,
            self._ts("MemMap Threshold:"),
            self.memmap_threshold_spin,
        )

        # View-resolution compositing (replaces the retired SSO overlay row)
        sharp_vector_text = self._ts("Enable sharp vector rendering (zoom)")
        self.sharp_vector_check = QCheckBox(sharp_vector_text)
        self._translation_binder.register(self.sharp_vector_check.setText, sharp_vector_text)
        sharp_vector_tip = self._ts(
            "Composites the display at view resolution so vector and text "
            "layers stay sharp when zoomed in, regardless of layer order."
        )
        self.sharp_vector_check.setToolTip(sharp_vector_tip)
        self._translation_binder.register(self.sharp_vector_check.setToolTip, sharp_vector_tip)
        perf_layout.addRow(self.sharp_vector_check)

        perf_group.setLayout(perf_layout)
        layout.addWidget(perf_group)

    def _setup_ui_settings_group(self, layout: QVBoxLayout) -> None:
        # UI settings
        ui_group = QGroupBox()
        ui_title = self._ts("UI Settings")
        self._translation_binder.register(ui_group.setTitle, ui_title)
        ui_layout = QFormLayout()

        # Theme
        self.theme_combo = QComboBox()
        self._populate_theme_combo()
        self._add_labeled_row(ui_layout, self._ts("Theme:"), self.theme_combo)

        # Toolbar
        toolbar_text = self._ts("Show Toolbar")
        self.show_toolbar_check = QCheckBox(toolbar_text)
        self._translation_binder.register(self.show_toolbar_check.setText, toolbar_text)
        ui_layout.addRow(self.show_toolbar_check)

        # Status bar
        status_text = self._ts("Show Status Bar")
        self.show_statusbar_check = QCheckBox(status_text)
        self._translation_binder.register(self.show_statusbar_check.setText, status_text)
        ui_layout.addRow(self.show_statusbar_check)

        ui_group.setLayout(ui_layout)
        layout.addWidget(ui_group)

    def _setup_io_settings_group(self, layout: QVBoxLayout) -> None:
        # File I/O settings
        io_group = QGroupBox()
        io_title = self._ts("File I/O Settings")
        self._translation_binder.register(io_group.setTitle, io_title)
        io_layout = QFormLayout()

        # Autosave
        autosave_text = self._ts("Enable Autosave")
        self.autosave_check = QCheckBox(autosave_text)
        self._translation_binder.register(self.autosave_check.setText, autosave_text)
        io_layout.addRow(self.autosave_check)

        # Autosave interval
        self.autosave_interval_spin = QSpinBox()
        self.autosave_interval_spin.setRange(30, 3600)
        interval_suffix = self._ts(" sec")
        self.autosave_interval_spin.setSuffix(interval_suffix)
        self._translation_binder.register(self.autosave_interval_spin.setSuffix, interval_suffix)
        self._add_labeled_row(
            io_layout,
            self._ts("Autosave Interval:"),
            self.autosave_interval_spin,
        )

        # Compression quality
        self.compression_slider = QSlider(Qt.Orientation.Horizontal)
        self.compression_slider.setRange(0, 100)
        self.compression_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.compression_slider.setTickInterval(10)

        compression_layout = QHBoxLayout()
        compression_layout.addWidget(self.compression_slider)
        self.compression_label = QLabel("95")
        compression_layout.addWidget(self.compression_label)

        self._add_labeled_row(io_layout, self._ts("JPEG Quality:"), compression_layout)

        # Default format
        self.format_combo = QComboBox()
        self.format_combo.addItems(["png", "jpg", "tiff", "webp"])
        self._add_labeled_row(io_layout, self._ts("Default Format:"), self.format_combo)

        # Layout directory selector
        layout_dir_row = QHBoxLayout()
        self.layout_dir_edit = QLineEdit()
        self.layout_dir_edit.setPlaceholderText(str(default_layout_directory(create=False)))
        browse_text = self._ts("Browse...")
        self.layout_dir_browse = QPushButton(browse_text)
        self._translation_binder.register(self.layout_dir_browse.setText, browse_text)
        layout_dir_row.addWidget(self.layout_dir_edit)
        layout_dir_row.addWidget(self.layout_dir_browse)
        self._add_labeled_row(io_layout, self._ts("Layout Directory:"), layout_dir_row)

        io_group.setLayout(io_layout)
        layout.addWidget(io_group)

    def _setup_control_buttons(self, layout: QVBoxLayout) -> None:
        # Control buttons
        button_layout = QHBoxLayout()

        apply_text = self._ts("Apply")
        self.apply_btn = QPushButton(apply_text)
        self._translation_binder.register(self.apply_btn.setText, apply_text)
        self.apply_btn.setObjectName("primary")
        button_layout.addWidget(self.apply_btn)

        save_text = self._ts("Save")
        self.save_btn = QPushButton(save_text)
        self._translation_binder.register(self.save_btn.setText, save_text)
        button_layout.addWidget(self.save_btn)

        reset_text = self._ts("Reset to Defaults")
        self.reset_btn = QPushButton(reset_text)
        self._translation_binder.register(self.reset_btn.setText, reset_text)
        button_layout.addWidget(self.reset_btn)

        layout.addLayout(button_layout)

    def _add_labeled_row(self, layout: QFormLayout, text: str, widget: QWidget | QLayout) -> None:
        label = QLabel()
        self._translation_binder.register(label.setText, text)
        if isinstance(widget, QLayout):
            layout.addRow(label, widget)
        else:
            layout.addRow(label, widget)

    def _subscribe_events(self) -> None:
        handler = self._on_language_changed
        self._event_handlers.append(handler)
        self._event_tokens.append(self._event_bus.on(Events.LANGUAGE_CHANGED, handler))

    def _on_language_changed(self, _payload: object) -> None:
        self.refresh_translations()

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        self._populate_theme_combo()

    def _populate_theme_combo(self) -> None:
        current_value = self.theme_combo.currentData() if hasattr(self, "theme_combo") else None
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        for key in ("dark", "light", "system"):
            if key == "light":
                label = self._translate_runtime("Light")
            else:
                label = self._translate_runtime(key)
            self.theme_combo.addItem(label, key)
        if isinstance(current_value, str):
            index = self.theme_combo.findData(current_value)
            if index >= 0:
                self.theme_combo.setCurrentIndex(index)
        self.theme_combo.blockSignals(False)

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("SettingsPanel", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def _connect_signals(self) -> None:
        """Connect widget signals."""
        # Buttons
        self.apply_btn.clicked.connect(self.apply_settings)
        self.save_btn.clicked.connect(self.save_settings)
        self.reset_btn.clicked.connect(self.reset_to_defaults)

        # Update compression label
        self.compression_slider.valueChanged.connect(
            lambda v: self.compression_label.setText(str(v))
        )
        self.layout_dir_edit.editingFinished.connect(
            lambda: self._on_layout_directory_changed(self.layout_dir_edit.text())
        )
        self.layout_dir_browse.clicked.connect(self._choose_layout_directory)

        # Auto-apply for checkboxes
        self.use_numba_check.toggled.connect(
            lambda: self.settings_changed.emit(
                "performance/use_numba",
                self.use_numba_check.isChecked(),
            )
        )
        self.sharp_vector_check.toggled.connect(
            lambda: self.settings_changed.emit(
                "performance/view_resolution_compositing_enabled",
                self.sharp_vector_check.isChecked(),
            )
        )

    def _load_settings(self) -> None:
        """Load settings from manager."""
        # Canvas settings
        canvas_config = self.settings_manager.get_canvas_config()
        self.width_spin.setValue(canvas_config.get("width", 832))
        self.height_spin.setValue(canvas_config.get("height", 1216))
        self.dpi_spin.setValue(canvas_config.get("dpi", 96))

        tile_size = str(canvas_config.get("tile_size", 256))
        index = self.tile_size_combo.findText(tile_size)
        if index >= 0:
            self.tile_size_combo.setCurrentIndex(index)

        self.max_layers_spin.setValue(canvas_config.get("max_layers", 10))

        # Performance settings
        self.use_numba_check.setChecked(
            bool(self.settings_manager.get("performance/use_numba", True))
        )
        self.cache_size_spin.setValue(
            cast(int, self.settings_manager.get("performance/cache_size", 100))
        )
        self.undo_steps_spin.setValue(
            cast(int, self.settings_manager.get("performance/max_undo_steps", 50))
        )

        threshold_bytes = canvas_config.get("use_memmap_threshold", 10_000_000)
        self.memmap_threshold_spin.setValue(threshold_bytes // 1_000_000)

        # View-resolution compositing (default ON since C3)
        self.sharp_vector_check.setChecked(
            bool(self.settings_manager.get("performance/view_resolution_compositing_enabled", True))
        )

        # UI settings
        theme_value = self.settings_manager.get("ui/theme", "dark")
        theme_index = self.theme_combo.findData(theme_value)
        if theme_index >= 0:
            self.theme_combo.setCurrentIndex(theme_index)
        self.show_toolbar_check.setChecked(bool(self.settings_manager.get("ui/show_toolbar", True)))
        self.show_statusbar_check.setChecked(
            bool(self.settings_manager.get("ui/show_statusbar", True))
        )

        # I/O settings
        self.autosave_check.setChecked(bool(self.settings_manager.get("io/autosave_enabled", True)))
        self.autosave_interval_spin.setValue(
            cast(int, self.settings_manager.get("io/autosave_interval", 300))
        )
        self.compression_slider.setValue(
            cast(int, self.settings_manager.get("io/compression_quality", 95))
        )
        self.compression_label.setText(str(self.settings_manager.get("io/compression_quality", 95)))
        self.format_combo.setCurrentText(
            cast(str, self.settings_manager.get("io/default_format", "png"))
        )
        layout_dir = self.settings_manager.get(
            "io/layout_directory",
            str(default_layout_directory()),
        )
        self.layout_dir_edit.setText(str(layout_dir))

    @Slot()
    def apply_settings(self) -> None:
        """Apply current settings without saving."""
        # Emit changes for immediate application
        self.settings_changed.emit("canvas/width", self.width_spin.value())
        self.settings_changed.emit("canvas/height", self.height_spin.value())
        self.settings_changed.emit("canvas/dpi", self.dpi_spin.value())
        self.settings_changed.emit("canvas/tile_size", int(self.tile_size_combo.currentText()))
        self.settings_changed.emit("canvas/max_layers", self.max_layers_spin.value())
        threshold_bytes = self.memmap_threshold_spin.value() * 1_000_000
        self.settings_changed.emit("canvas/use_memmap_threshold", threshold_bytes)

    @Slot()
    def save_settings(self) -> None:
        """Save all settings."""
        # Canvas settings
        self.settings_manager.set("canvas/width", self.width_spin.value())
        self.settings_manager.set("canvas/height", self.height_spin.value())
        self.settings_manager.set("canvas/dpi", self.dpi_spin.value())
        self.settings_manager.set("canvas/tile_size", int(self.tile_size_combo.currentText()))
        self.settings_manager.set("canvas/max_layers", self.max_layers_spin.value())
        self.settings_manager.set(
            "canvas/use_memmap_threshold", self.memmap_threshold_spin.value() * 1_000_000
        )

        # Performance settings
        self.settings_manager.set("performance/use_numba", self.use_numba_check.isChecked())
        self.settings_manager.set("performance/cache_size", self.cache_size_spin.value())
        self.settings_manager.set("performance/max_undo_steps", self.undo_steps_spin.value())
        self.settings_manager.set(
            "performance/view_resolution_compositing_enabled",
            self.sharp_vector_check.isChecked(),
        )

        # UI settings
        current_theme = self.theme_combo.currentData()
        self.settings_manager.set(
            "ui/theme",
            current_theme if isinstance(current_theme, str) else self.theme_combo.currentText(),
        )
        self.settings_manager.set("ui/show_toolbar", self.show_toolbar_check.isChecked())
        self.settings_manager.set("ui/show_statusbar", self.show_statusbar_check.isChecked())

        # I/O settings
        self.settings_manager.set("io/autosave_enabled", self.autosave_check.isChecked())
        self.settings_manager.set("io/autosave_interval", self.autosave_interval_spin.value())
        self.settings_manager.set("io/compression_quality", self.compression_slider.value())
        self.settings_manager.set("io/default_format", self.format_combo.currentText())
        layout_dir_value = self.layout_dir_edit.text().strip()
        if layout_dir_value:
            self.settings_manager.set("io/layout_directory", layout_dir_value)

    @Slot()
    def reset_to_defaults(self) -> None:
        """Reset all settings to defaults."""
        self.reset_requested.emit()

        # Clear and reinitialize settings
        self.settings_manager.clear()
        self._load_settings()

    def _choose_layout_directory(self) -> None:
        """Prompt the user to select a layout directory."""
        start_dir = self.layout_dir_edit.text().strip()
        if not start_dir:
            start_dir = str(default_layout_directory())
        directory = QFileDialog.getExistingDirectory(
            self,
            self.tr("Select Layout Directory"),
            start_dir,
        )
        if directory:
            self.layout_dir_edit.setText(directory)
            self._on_layout_directory_changed(directory)

    def _on_layout_directory_changed(self, path: str) -> None:
        """Emit a settings change when the layout directory input changes."""
        normalized = path.strip()
        if normalized:
            self.settings_changed.emit("io/layout_directory", normalized)


if TYPE_CHECKING:  # pragma: no cover - typing aid only
    SettingsPanel.settings_changed = cast(  # type: ignore[assignment]
        Signal2[str, object], SettingsPanel.settings_changed
    )
    SettingsPanel.reset_requested = cast(  # type: ignore[assignment]
        Signal0, SettingsPanel.reset_requested
    )
