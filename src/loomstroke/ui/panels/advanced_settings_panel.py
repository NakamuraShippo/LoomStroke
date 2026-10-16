"""
Advanced settings panel for application configuration.

Per Master.md PR-D specifications, provides UI for all settings
with bidirectional binding and validation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QEvent, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.event_bus_extensions import get_extended_event_bus
from ...core.events import Events
from ...platform.settings_schema import DEFAULT_COMFYUI_WS
from ..translations import TranslationBinder, translate_with_fallback, translated_literal

logger = logging.getLogger(__name__)


class AdvancedSettingsPanel(QWidget):
    """
    Advanced settings panel with categorized configuration options.

    Per PR-D spec, provides bidirectional binding with SettingsManager.
    """

    # Signals
    settings_changed = Signal(str, object)  # key, value
    apply_requested = Signal()
    reset_requested = Signal()

    def __init__(
        self,
        settings_manager: Any | None = None,
        event_bus: Any | None = None,
        parent: QWidget | None = None,
    ):
        """
        Initialize settings panel.

        Args:
            settings_manager: SettingsManager instance
            event_bus: EventBus instance for communication
            parent: Parent widget
        """
        super().__init__(parent)

        self.settings_manager = settings_manager
        self.event_bus = event_bus or get_extended_event_bus()
        self.widgets: dict[str, QWidget] = {}
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._event_handlers: list[Any] = []
        self._event_tokens: list[Any] = []
        self._invalid_value_text = self._ts("Invalid value")
        self._translation_binder.register(self._set_invalid_value_text, self._invalid_value_text)

        self._setup_ui()
        self._load_settings()
        self._connect_signals()
        self._subscribe_events()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        # Header
        header_layout = QHBoxLayout()

        title_text = self._ts("Settings")
        title = QLabel(title_text)
        self._translation_binder.register(title.setText, title_text)
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        apply_text = self._ts("Apply")
        apply_btn = QPushButton(apply_text)
        self._translation_binder.register(apply_btn.setText, apply_text)
        apply_btn.clicked.connect(self._on_apply)
        header_layout.addWidget(apply_btn)

        reset_text = self._ts("Reset")
        reset_btn = QPushButton(reset_text)
        self._translation_binder.register(reset_btn.setText, reset_text)
        reset_btn.clicked.connect(self._on_reset)
        header_layout.addWidget(reset_btn)

        layout.addLayout(header_layout)

        # Tab widget for categories
        self.tab_widget = QTabWidget()

        # Connection tab
        self.connection_tab = self._create_connection_tab()
        connection_title = self._ts("Connection")
        self.tab_widget.addTab(self.connection_tab, connection_title)
        self._register_tab_title(self.connection_tab, connection_title)

        # Render tab
        self.render_tab = self._create_render_tab()
        render_title = self._ts("Render")
        self.tab_widget.addTab(self.render_tab, render_title)
        self._register_tab_title(self.render_tab, render_title)

        # UI tab
        self.ui_tab = self._create_ui_tab()
        ui_title = self._ts("Interface")
        self.tab_widget.addTab(self.ui_tab, ui_title)
        self._register_tab_title(self.ui_tab, ui_title)

        # Performance tab
        self.performance_tab = self._create_performance_tab()
        performance_title = self._ts("Performance")
        self.tab_widget.addTab(self.performance_tab, performance_title)
        self._register_tab_title(self.performance_tab, performance_title)

        # Make scrollable
        scroll = QScrollArea()
        scroll.setWidget(self.tab_widget)
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(scroll)

    def _register_tab_title(self, widget: QWidget, title: str) -> None:
        index = self.tab_widget.indexOf(widget)
        if index < 0:
            return

        def set_tab_title(text: str, idx: int = index) -> None:
            self.tab_widget.setTabText(idx, text)

        self._translation_binder.register(
            set_tab_title,
            title,
        )

    def _add_labeled_row(self, layout: QFormLayout, text: str, widget: QWidget) -> None:
        label = QLabel()
        self._translation_binder.register(label.setText, text)
        layout.addRow(label, widget)

    def _set_invalid_value_text(self, text: str) -> None:
        self._invalid_value_text = text

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        self._populate_theme_combo()

    def _create_connection_tab(self) -> QWidget:
        """Create connection settings tab."""
        widget = QWidget()
        layout = QFormLayout(widget)

        # Connection group
        group = QGroupBox()
        group_title = self._ts("ComfyUI Connection")
        self._translation_binder.register(group.setTitle, group_title)
        group_layout = QFormLayout(group)

        # Base URL
        url_edit = QLineEdit()
        url_edit.setPlaceholderText(DEFAULT_COMFYUI_WS)
        self.widgets["connection/base_url"] = url_edit
        self._add_labeled_row(group_layout, self._ts("Base URL:"), url_edit)

        # TLS verification
        tls_text = self._ts("Verify TLS")
        tls_check = QCheckBox(tls_text)
        self._translation_binder.register(tls_check.setText, tls_text)
        self.widgets["connection/verify_tls"] = tls_check
        self._add_labeled_row(group_layout, self._ts("Security:"), tls_check)

        # Timeout
        timeout_spin = QSpinBox()
        timeout_spin.setRange(1, 300)
        timeout_suffix = self._ts(" sec")
        timeout_spin.setSuffix(timeout_suffix)
        self._translation_binder.register(timeout_spin.setSuffix, timeout_suffix)
        self.widgets["connection/timeout_s"] = timeout_spin
        self._add_labeled_row(group_layout, self._ts("Timeout:"), timeout_spin)

        # Max retries
        retries_spin = QSpinBox()
        retries_spin.setRange(0, 10)
        self.widgets["connection/max_retries"] = retries_spin
        self._add_labeled_row(group_layout, self._ts("Max Retries:"), retries_spin)

        layout.addRow(group)

        return widget

    def _create_render_tab(self) -> QWidget:
        """Create render settings tab."""
        widget = QWidget()
        layout = QFormLayout(widget)

        # Render group
        group = QGroupBox()
        group_title = self._ts("Render Settings")
        self._translation_binder.register(group.setTitle, group_title)
        group_layout = QFormLayout(group)

        # Tile size
        tile_spin = QSpinBox()
        tile_spin.setRange(64, 1024)
        tile_spin.setSingleStep(64)
        tile_suffix = self._ts(" px")
        tile_spin.setSuffix(tile_suffix)
        self._translation_binder.register(tile_spin.setSuffix, tile_suffix)
        self.widgets["render/tile_size"] = tile_spin
        self._add_labeled_row(group_layout, self._ts("Tile Size:"), tile_spin)

        # Cache size
        cache_spin = QSpinBox()
        cache_spin.setRange(10, 1000)
        cache_suffix = self._ts(" MB")
        cache_spin.setSuffix(cache_suffix)
        self._translation_binder.register(cache_spin.setSuffix, cache_suffix)
        self.widgets["render/cache_size"] = cache_spin
        self._add_labeled_row(group_layout, self._ts("Cache Size:"), cache_spin)

        layout.addRow(group)

        return widget

    def _create_ui_tab(self) -> QWidget:
        """Create UI settings tab."""
        widget = QWidget()
        layout = QFormLayout(widget)

        # UI group
        group = QGroupBox()
        group_title = self._ts("User Interface")
        self._translation_binder.register(group.setTitle, group_title)
        group_layout = QFormLayout(group)

        # Theme
        theme_combo = QComboBox()
        self._populate_theme_combo(theme_combo)
        self.widgets["ui/theme"] = theme_combo
        self._add_labeled_row(group_layout, self._ts("Theme:"), theme_combo)

        # Toolbar
        toolbar_text = self._ts("Show Toolbar")
        toolbar_check = QCheckBox(toolbar_text)
        self._translation_binder.register(toolbar_check.setText, toolbar_text)
        self.widgets["ui/show_toolbar"] = toolbar_check
        self._add_labeled_row(group_layout, self._ts("Toolbar:"), toolbar_check)

        # Status bar
        statusbar_text = self._ts("Show Status Bar")
        statusbar_check = QCheckBox(statusbar_text)
        self._translation_binder.register(statusbar_check.setText, statusbar_text)
        self.widgets["ui/show_statusbar"] = statusbar_check
        self._add_labeled_row(group_layout, self._ts("Status Bar:"), statusbar_check)

        layout.addRow(group)

        return widget

    def _create_performance_tab(self) -> QWidget:
        """Create performance settings tab."""
        widget = QWidget()
        layout = QFormLayout(widget)

        # Performance group
        group = QGroupBox()
        group_title = self._ts("Performance")
        self._translation_binder.register(group.setTitle, group_title)
        group_layout = QFormLayout(group)

        # Numba JIT
        numba_text = self._ts("Use Numba JIT")
        numba_check = QCheckBox(numba_text)
        self._translation_binder.register(numba_check.setText, numba_text)
        self.widgets["performance/use_numba"] = numba_check
        self._add_labeled_row(group_layout, self._ts("JIT Compiler:"), numba_check)

        # Undo steps
        undo_spin = QSpinBox()
        undo_spin.setRange(1, 1000)
        self.widgets["performance/max_undo_steps"] = undo_spin
        self._add_labeled_row(group_layout, self._ts("Max Undo Steps:"), undo_spin)

        layout.addRow(group)

        # I/O group
        io_group = QGroupBox()
        io_title = self._ts("File I/O")
        self._translation_binder.register(io_group.setTitle, io_title)
        io_layout = QFormLayout(io_group)

        # Autosave
        autosave_text = self._ts("Enable Autosave")
        autosave_check = QCheckBox(autosave_text)
        self._translation_binder.register(autosave_check.setText, autosave_text)
        self.widgets["io/autosave_enabled"] = autosave_check
        self._add_labeled_row(io_layout, self._ts("Autosave:"), autosave_check)

        # Autosave interval
        interval_spin = QSpinBox()
        interval_spin.setRange(30, 3600)
        interval_spin.setSingleStep(30)
        interval_suffix = self._ts(" sec")
        interval_spin.setSuffix(interval_suffix)
        self._translation_binder.register(interval_spin.setSuffix, interval_suffix)
        self.widgets["io/autosave_interval"] = interval_spin
        self._add_labeled_row(io_layout, self._ts("Interval:"), interval_spin)

        layout.addRow(io_group)

        return widget

    def _load_settings(self) -> None:
        """Load current settings into UI widgets."""
        if not self.settings_manager:
            return

        for key, widget in self.widgets.items():
            try:
                if isinstance(widget, QCheckBox):
                    value = self.settings_manager.get_bool(key)
                    widget.setChecked(value)

                elif isinstance(widget, QSpinBox):
                    value = self.settings_manager.get_int(key)
                    widget.setValue(value)

                elif isinstance(widget, QLineEdit):
                    value = self.settings_manager.get_str(key)
                    widget.setText(value)

                elif isinstance(widget, QComboBox):
                    value = self.settings_manager.get_str(key)
                    index = widget.findData(value)
                    if index < 0:
                        index = widget.findText(value)
                    if index >= 0:
                        widget.setCurrentIndex(index)

            except Exception as e:
                logger.warning(f"Failed to load setting {key}: {e}")

    def _connect_signals(self) -> None:
        """Connect widget signals for change detection."""
        for key, widget in self.widgets.items():
            if isinstance(widget, QCheckBox):
                widget.toggled.connect(lambda checked, k=key: self._on_setting_changed(k, checked))
            elif isinstance(widget, QSpinBox):
                widget.valueChanged.connect(lambda value, k=key: self._on_setting_changed(k, value))
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(lambda text, k=key: self._on_setting_changed(k, text))
            elif isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(
                    lambda _text, k=key, w=widget: self._on_setting_changed(
                        k, w.currentData() if w.currentData() is not None else w.currentText()
                    )
                )

    def _subscribe_events(self) -> None:
        """Subscribe to EventBus events."""
        if not self.event_bus:
            return

        # Subscribe to external settings changes
        settings_handler = self._on_external_settings_change
        self._event_handlers.append(settings_handler)
        self._event_tokens.append(
            self.event_bus.on(
                Events.SETTINGS_CHANGED,
                settings_handler,
            )
        )
        self._subscribe_language_events()

    def _subscribe_language_events(self) -> None:
        if not self.event_bus:
            return
        handler = self._on_language_changed
        self._event_handlers.append(handler)
        self._event_tokens.append(self.event_bus.on(Events.LANGUAGE_CHANGED, handler))

    def _on_language_changed(self, _payload: Any) -> None:
        self.refresh_translations()

    def _on_setting_changed(self, key: str, value: Any) -> None:
        """
        Handle setting change from UI.

        Args:
            key: Setting key
            value: New value
        """
        self.settings_changed.emit(key, value)

        # Validate if settings manager available
        if self.settings_manager:
            is_valid, error_msg = self.settings_manager.validate(key, value)

            # Show validation feedback
            widget = self.widgets.get(key)
            if widget:
                if is_valid:
                    widget.setStyleSheet("")
                else:
                    widget.setStyleSheet("border: 1px solid red;")
                    widget.setToolTip(error_msg or self._invalid_value_text)

    def _populate_theme_combo(self, combo: QComboBox | None = None) -> None:
        target = combo or cast(QComboBox | None, self.widgets.get("ui/theme"))
        if target is None:
            return
        current_value = target.currentData()
        target.blockSignals(True)
        target.clear()
        for key in ("system", "light", "dark"):
            if key == "light":
                label = self._translate_runtime("Light")
            else:
                label = self._translate_runtime(key)
            target.addItem(label, key)
        if isinstance(current_value, str):
            index = target.findData(current_value)
            if index >= 0:
                target.setCurrentIndex(index)
        target.blockSignals(False)

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("AdvancedSettingsPanel", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def _on_external_settings_change(self, event: Any) -> None:
        """
        Handle settings change from external source.

        Args:
            event: SettingsChangeEvent
        """
        # Update UI if key exists in widgets
        if hasattr(event, "key") and event.key in self.widgets:
            widget = self.widgets[event.key]

            # Update widget value without triggering signals
            widget.blockSignals(True)
            try:
                if isinstance(widget, QCheckBox):
                    widget.setChecked(bool(event.new_value))
                elif isinstance(widget, QSpinBox):
                    widget.setValue(int(event.new_value))
                elif isinstance(widget, QLineEdit):
                    widget.setText(str(event.new_value))
                elif isinstance(widget, QComboBox):
                    index = widget.findText(str(event.new_value))
                    if index >= 0:
                        widget.setCurrentIndex(index)
            finally:
                widget.blockSignals(False)

    @Slot()
    def _on_apply(self) -> None:
        """Apply all changed settings."""
        if not self.settings_manager:
            return

        # Save all settings
        for key, widget in self.widgets.items():
            try:
                value: Any
                if isinstance(widget, QCheckBox):
                    value = widget.isChecked()
                elif isinstance(widget, QSpinBox):
                    value = widget.value()
                elif isinstance(widget, QLineEdit):
                    value = widget.text()
                elif isinstance(widget, QComboBox):
                    value = (
                        widget.currentData()
                        if widget.currentData() is not None
                        else widget.currentText()
                    )
                else:
                    continue

                # Validate and set
                if not self.settings_manager.set_validated(key, value):
                    logger.warning(f"Failed to apply setting {key}={value}")

            except Exception as e:
                logger.error(f"Error applying setting {key}: {e}")

        self.apply_requested.emit()
        logger.info("Settings applied")

    @Slot()
    def _on_reset(self) -> None:
        """Reset settings to defaults."""
        if not self.settings_manager:
            return

        # Clear and reinitialize
        self.settings_manager.clear()

        # Reload UI
        self._load_settings()

        self.reset_requested.emit()
        logger.info("Settings reset to defaults")


if TYPE_CHECKING:  # pragma: no cover - typing aid only
    from loomstroke.ui.typing import Signal0, Signal2

    AdvancedSettingsPanel.settings_changed = cast(  # type: ignore[assignment]
        Signal2[str, object],
        AdvancedSettingsPanel.settings_changed,
    )
    AdvancedSettingsPanel.apply_requested = cast(  # type: ignore[assignment]
        Signal0,
        AdvancedSettingsPanel.apply_requested,
    )
    AdvancedSettingsPanel.reset_requested = cast(  # type: ignore[assignment]
        Signal0,
        AdvancedSettingsPanel.reset_requested,
    )
