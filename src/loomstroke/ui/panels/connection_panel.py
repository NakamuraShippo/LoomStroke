"""
Connection panel for AI service management.

Handles connection to ComfyUI and other AI services.
Per Master.md, this panel is under 400 lines.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QEvent, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.event_bus_extensions import get_extended_event_bus
from ...core.events import Events
from ...platform import AsyncManager, SettingsManager
from ...platform.settings_schema import (
    DEFAULT_COMFYUI_HOST,
    DEFAULT_COMFYUI_PORT,
    DEFAULT_COMFYUI_URL,
    SettingsValue,
)
from ...plugins.comfyui.async_connection import AsyncComfyUIConnection, ConnectionStatus
from ...plugins.common.outbound_policy import ensure_network_services_consent
from ..translations import TranslationBinder, translate_with_fallback, translated_literal

if TYPE_CHECKING:
    from ..typing import Signal0, Signal1


class ConnectionPanel(QWidget):
    """
    Panel for managing AI service connections.

    Replaces connection management from comfy_panel.py.
    """

    # Signals
    connection_changed = Signal(ConnectionStatus)
    test_requested = Signal()

    def __init__(
        self,
        async_manager: AsyncManager,
        settings_manager: SettingsManager,
        parent: QWidget | None = None,
    ):
        """
        Initialize connection panel.

        Args:
            async_manager: AsyncManager instance
            settings_manager: SettingsManager instance
            parent: Parent widget
        """
        super().__init__(parent)

        self.async_manager = async_manager
        self.settings_manager = settings_manager
        self.connection: AsyncComfyUIConnection | None = None
        self._event_bus = get_extended_event_bus()
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._event_handlers: list[Any] = []
        self._event_tokens: list[Any] = []
        self._status_state = "not_connected"
        self._status_text_not_connected = self._ts("Not Connected")
        self._status_text_connected = self._ts("Connected")
        self._status_text_disconnected = self._ts("Disconnected")
        self._status_text_error = self._ts("Error")
        self._url_label_template = self._ts("URL: {url}")
        self._url_not_configured = self._ts("URL: Not configured")
        self._translation_binder.register(
            self._set_status_text_not_connected, self._status_text_not_connected
        )
        self._translation_binder.register(
            self._set_status_text_connected, self._status_text_connected
        )
        self._translation_binder.register(
            self._set_status_text_disconnected, self._status_text_disconnected
        )
        self._translation_binder.register(self._set_status_text_error, self._status_text_error)
        self._translation_binder.register(self._set_url_label_template, self._url_label_template)
        self._translation_binder.register(self._set_url_not_configured, self._url_not_configured)

        self._setup_ui()
        self._load_settings()
        self._connect_signals()
        self._subscribe_events()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        self._setup_connection_group(layout)
        self._setup_status_group(layout)
        self._setup_control_buttons(layout)
        layout.addStretch()

    def _setup_connection_group(self, layout: QVBoxLayout) -> None:
        # Connection group
        conn_group = QGroupBox()
        conn_title = self._ts("Connection Settings")
        self._translation_binder.register(conn_group.setTitle, conn_title)
        conn_layout = QFormLayout()

        # Protocol selection
        self.protocol_combo = QComboBox()
        self.protocol_combo.addItems(["http", "https"])
        self._add_labeled_row(conn_layout, self._ts("Protocol:"), self.protocol_combo)

        # Address input
        self.address_input = QLineEdit()
        address_placeholder = self._ts(f"{DEFAULT_COMFYUI_HOST}:{DEFAULT_COMFYUI_PORT}")
        self.address_input.setPlaceholderText(address_placeholder)
        self._translation_binder.register(
            self.address_input.setPlaceholderText,
            address_placeholder,
        )
        self._add_labeled_row(conn_layout, self._ts("Address:"), self.address_input)

        # Timeout
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.setValue(30)
        timeout_suffix = self._ts(" sec")
        self.timeout_spin.setSuffix(timeout_suffix)
        self._translation_binder.register(self.timeout_spin.setSuffix, timeout_suffix)
        self._add_labeled_row(conn_layout, self._ts("Timeout:"), self.timeout_spin)

        # Max retries
        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 10)
        self.retries_spin.setValue(3)
        self._add_labeled_row(conn_layout, self._ts("Max Retries:"), self.retries_spin)

        conn_group.setLayout(conn_layout)
        layout.addWidget(conn_group)

    def _setup_status_group(self, layout: QVBoxLayout) -> None:
        # Status group
        status_group = QGroupBox()
        status_title = self._ts("Connection Status")
        self._translation_binder.register(status_group.setTitle, status_title)
        status_layout = QVBoxLayout()

        # Status label
        self.status_label = QLabel(self._status_text_not_connected)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_status_style(self.status_label)
        status_layout.addWidget(self.status_label)

        # URL display
        self.url_label = QLabel(self._url_not_configured)
        self.url_label.setWordWrap(True)
        status_layout.addWidget(self.url_label)

        status_group.setLayout(status_layout)
        layout.addWidget(status_group)

    def _setup_control_buttons(self, layout: QVBoxLayout) -> None:
        button_layout = QHBoxLayout()

        connect_text = self._ts("Connect")
        self.connect_btn = QPushButton(connect_text)
        self._translation_binder.register(self.connect_btn.setText, connect_text)
        self.connect_btn.setObjectName("primary")
        button_layout.addWidget(self.connect_btn)

        disconnect_text = self._ts("Disconnect")
        self.disconnect_btn = QPushButton(disconnect_text)
        self._translation_binder.register(self.disconnect_btn.setText, disconnect_text)
        self.disconnect_btn.setEnabled(False)
        button_layout.addWidget(self.disconnect_btn)

        test_text = self._ts("Test")
        self.test_btn = QPushButton(test_text)
        self._translation_binder.register(self.test_btn.setText, test_text)
        button_layout.addWidget(self.test_btn)

        save_text = self._ts("Save")
        self.save_btn = QPushButton(save_text)
        self._translation_binder.register(self.save_btn.setText, save_text)
        button_layout.addWidget(self.save_btn)

        layout.addLayout(button_layout)

    def _apply_status_style(self, label: QLabel) -> None:
        label.setStyleSheet("""
            QLabel {
                padding: 10px;
                border: 2px solid #555;
                border-radius: 5px;
                background-color: #2b2b2b;
                color: #888;
                font-weight: bold;
            }
            """)

    def _add_labeled_row(self, layout: QFormLayout, text: str, widget: QWidget) -> None:
        label = QLabel()
        self._translation_binder.register(label.setText, text)
        layout.addRow(label, widget)

    def _set_status_text_not_connected(self, text: str) -> None:
        self._status_text_not_connected = text

    def _set_status_text_connected(self, text: str) -> None:
        self._status_text_connected = text

    def _set_status_text_disconnected(self, text: str) -> None:
        self._status_text_disconnected = text

    def _set_status_text_error(self, text: str) -> None:
        self._status_text_error = text

    def _set_url_label_template(self, text: str) -> None:
        self._url_label_template = text

    def _set_url_not_configured(self, text: str) -> None:
        self._url_not_configured = text

    def _subscribe_events(self) -> None:
        handler = self._on_language_changed
        self._event_handlers.append(handler)
        self._event_tokens.append(self._event_bus.on(Events.LANGUAGE_CHANGED, handler))

    def _on_language_changed(self, _payload: object) -> None:
        self.refresh_translations()

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        self._apply_status_text()
        self._update_url_display()

    def _apply_status_text(self) -> None:
        if self._status_state == "connected":
            self.status_label.setText(self._status_text_connected)
        elif self._status_state == "disconnected":
            self.status_label.setText(self._status_text_disconnected)
        elif self._status_state == "error":
            self.status_label.setText(self._status_text_error)
        else:
            self.status_label.setText(self._status_text_not_connected)

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("ConnectionPanel", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def _connect_signals(self) -> None:
        """Connect widget signals."""
        self.connect_btn.clicked.connect(self.on_connect)
        self.disconnect_btn.clicked.connect(self.on_disconnect)
        self.test_btn.clicked.connect(self.on_test)
        self.save_btn.clicked.connect(self.save_settings)

        # Update URL when settings change
        self.protocol_combo.currentTextChanged.connect(self._update_url_display)
        self.address_input.textChanged.connect(self._update_url_display)

    def _load_settings(self) -> None:
        """Load connection settings."""
        conn_settings = self.settings_manager.get_connection_settings()

        # Parse base URL
        base_url = conn_settings.get("base_url", DEFAULT_COMFYUI_URL)
        base_url = base_url.strip()

        # Extract protocol
        if base_url.startswith("https://"):
            self.protocol_combo.setCurrentText("https")
            base_url = base_url[8:]
        else:
            self.protocol_combo.setCurrentText("http")
            if base_url.startswith("http://"):
                base_url = base_url[7:]
        base_url = base_url.strip().rstrip("/")
        if not base_url:
            base_url = f"{DEFAULT_COMFYUI_HOST}:{DEFAULT_COMFYUI_PORT}"
        self.address_input.setText(base_url)

        # Load other settings
        timeout_value = conn_settings.get("timeout", 30)
        self.timeout_spin.setValue(cast(int, timeout_value))
        self.retries_spin.setValue(conn_settings.get("max_retries", 3))

        self._update_url_display()

    def save_settings(self) -> None:
        """Save connection settings."""
        base_url = self.get_base_url()
        ws_url = base_url.replace("http", "ws") + "/ws"

        settings: dict[str, SettingsValue] = {
            "base_url": base_url,
            "ws_url": ws_url,
            "use_ssl": self.protocol_combo.currentText() == "https",
            "timeout": self.timeout_spin.value(),
            "max_retries": self.retries_spin.value(),
        }

        self.settings_manager.set_group("connection", settings)

        # Update async manager settings if available
        if hasattr(self.async_manager, "max_retries"):
            self.async_manager.max_retries = self.retries_spin.value()

        QMessageBox.information(
            self,
            self._translate_runtime("Settings"),
            self._translate_runtime("Connection settings saved."),
        )

    def get_base_url(self) -> str:
        """
        Get the base URL from current settings.

        Returns:
            Base URL string
        """
        protocol = self.protocol_combo.currentText()
        address = self._normalize_address_input(self.address_input.text())
        return f"{protocol}://{address}"

    def _normalize_address_input(self, address: str) -> str:
        cleaned = address.strip()
        if not cleaned:
            cleaned = f"{DEFAULT_COMFYUI_HOST}:{DEFAULT_COMFYUI_PORT}"
        if "://" in cleaned:
            _, _, cleaned = cleaned.partition("://")
        cleaned = cleaned.strip().rstrip("/")
        if cleaned.startswith("["):
            end = cleaned.find("]")
            if end != -1 and len(cleaned) == end + 1:
                cleaned = f"{cleaned}:{DEFAULT_COMFYUI_PORT}"
            return cleaned
        if ":" not in cleaned:
            cleaned = f"{cleaned}:{DEFAULT_COMFYUI_PORT}"
        return cleaned

    def _update_url_display(self) -> None:
        """Update URL display label."""
        url = self.get_base_url()
        self.url_label.setText(self._url_label_template.format(url=url))

    @Slot()
    def on_connect(self) -> None:
        """Handle connect button click."""
        if not ensure_network_services_consent(self.settings_manager, service_name="ComfyUI"):
            QMessageBox.warning(
                self,
                self._translate_runtime("Privacy"),
                self._translate_runtime(
                    "External service access is disabled. "
                    "Enable it in Preferences to connect to ComfyUI."
                ),
            )
            return

        if self.connection and self.connection.is_connected():
            return

        # Create new connection
        base_url = self.get_base_url()
        self.connection = AsyncComfyUIConnection(self.async_manager, base_url)

        # Connect signals
        self.connection.connected.connect(self._on_connected)
        self.connection.disconnected.connect(self._on_disconnected)
        self.connection.disconnected_unexpectedly.connect(self._on_disconnected_unexpectedly)
        self.connection.error_occurred.connect(self._on_error)

        # Run connection in async
        self.async_manager.run_async(self.connection.connect())

    @Slot()
    def on_disconnect(self) -> None:
        """Handle disconnect button click."""
        if not self.connection:
            return

        self.async_manager.run_async(self.connection.disconnect())

    @Slot()
    def on_test(self) -> None:
        """Handle test button click."""
        if not ensure_network_services_consent(self.settings_manager, service_name="ComfyUI"):
            QMessageBox.warning(
                self,
                self._translate_runtime("Privacy"),
                self._translate_runtime(
                    "External service access is disabled. "
                    "Enable it in Preferences before testing."
                ),
            )
            return

        base_url = self.get_base_url()
        self.test_requested.emit()
        self.async_manager.run_async(self._test_connection(base_url))

    async def _test_connection(self, base_url: str) -> None:
        """Run connection test asynchronously and report the outcome."""
        url = f"{base_url}/system_stats"
        success = await self.async_manager.test_connection(url)
        if success:
            QMessageBox.information(
                self,
                self._translate_runtime("Test"),
                self._translate_runtime("Connection successful!"),
            )
        else:
            QMessageBox.warning(
                self,
                self._translate_runtime("Test"),
                self._translate_runtime("Server did not respond with HTTP 200."),
            )

    @Slot()
    def _on_connected(self) -> None:
        """Handle successful connection."""
        self._status_state = "connected"
        self.status_label.setText(self._status_text_connected)
        self.status_label.setStyleSheet("""
            QLabel {
                padding: 10px;
                border: 2px solid #4CAF50;
                border-radius: 5px;
                background-color: #1b3a1b;
                color: #4CAF50;
                font-weight: bold;
            }
        """)

        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)

        if self.connection:
            self.connection_changed.emit(self.connection.get_status())

    @Slot()
    def _on_disconnected(self) -> None:
        """Handle disconnection."""
        self._status_state = "disconnected"
        self.status_label.setText(self._status_text_disconnected)
        self.status_label.setStyleSheet("""
            QLabel {
                padding: 10px;
                border: 2px solid #555;
                border-radius: 5px;
                background-color: #2b2b2b;
                color: #888;
                font-weight: bold;
            }
        """)

        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)

        if self.connection:
            self.connection_changed.emit(self.connection.get_status())

    @Slot(str)
    def _on_disconnected_unexpectedly(self, reason: str) -> None:
        """Handle an unexpected ComfyUI WebSocket drop.

        Emitted by ``AsyncComfyUIConnection._handle_messages()`` on the
        WSMsgType.CLOSED / WSMsgType.ERROR / except paths. The existing
        ``disconnected`` signal is already emitted alongside this one, so
        ``_on_disconnected()`` has already reset the status label and
        buttons by the time this slot runs. This slot only needs to surface
        the disconnect reason to the user via a non-blocking warning
        dialog (same style as ``_on_error``).

        Pre-release audit §12-C #5 / §12-N (2026-04-11).
        """
        title = self._translate_runtime("ComfyUI Disconnected")
        message = self._translate_runtime(
            "The connection to ComfyUI was lost unexpectedly:\n{reason}"
        ).format(reason=reason)
        QMessageBox.warning(self, title, message)

    @Slot(str)
    def _on_error(self, error: str) -> None:
        """Handle connection error."""
        self._status_state = "error"
        self.status_label.setText(self._status_text_error)
        self.status_label.setStyleSheet("""
            QLabel {
                padding: 10px;
                border: 2px solid #f44336;
                border-radius: 5px;
                background-color: #3a1b1b;
                color: #f44336;
                font-weight: bold;
            }
        """)

        QMessageBox.critical(self, self._translate_runtime("Connection Error"), error)

    def get_connection(self) -> AsyncComfyUIConnection | None:
        """
        Get current connection instance.

        Returns:
            Connection instance or None
        """
        return self.connection


if TYPE_CHECKING:  # pragma: no cover - typing aid only
    ConnectionPanel.connection_changed = cast(  # type: ignore[assignment]
        Signal1[ConnectionStatus], ConnectionPanel.connection_changed
    )
    ConnectionPanel.test_requested = cast(  # type: ignore[assignment]
        Signal0, ConnectionPanel.test_requested
    )
