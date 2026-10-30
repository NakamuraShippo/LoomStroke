"""ComfyUI connection management bar.

This module provides the connection status and control UI,
adapted from the legacy implementation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.event_bus_extensions import get_extended_event_bus
from loomstroke.core.events import Events, SettingsChangeEvent
from loomstroke.plugins.comfyui.server_registry import (
    DEFAULT_LOCAL_COMFYUI_URL,
    get_registry,
    sync_registry_from_settings,
)
from loomstroke.plugins.common.outbound_policy import is_external_services_allowed
from loomstroke.ui.translations import TranslationBinder, translated_literal_domain

if TYPE_CHECKING:
    from ..extended_service import ExtendedComfyUIService

logger = logging.getLogger(__name__)
DEFAULT_COMFYUI_ADDRESS = "127.0.0.1:8188"
DEFAULT_COMFYUI_PORT = 8188


class ConnectionBar(QWidget):
    """Connection management bar for ComfyUI.

    Provides server connection controls and status display.
    """

    # Signals
    connection_changed = Signal(bool)  # connected state
    settings_changed = Signal(dict)  # connection settings

    def __init__(
        self, service: ExtendedComfyUIService | None = None, parent: QWidget | None = None
    ):
        """Initialize connection bar.

        Args:
            service: ComfyUI service instance
            parent: Parent widget
        """
        super().__init__(parent)
        self.service = service
        self._connected = False
        self._connecting = False
        self._event_bus = get_extended_event_bus()
        self._translation_binder = TranslationBinder(self.tr)
        self._event_handlers: list[Any] = []
        self._event_tokens: list[Any] = []
        self._status_disconnected = self._ts("Disconnected")
        self._status_connecting = self._ts("Connecting...")
        self._status_connected = self._ts("Connected")
        self._status_failed = self._ts("Connection failed")
        self._queue_label_template = self._ts("Queue: {count}")
        self._connect_text = self._ts("Connect")
        self._disconnect_text = self._ts("Disconnect")
        self._queue_count_value: str | int = 0
        self._translation_binder.register(self._set_status_disconnected, self._status_disconnected)
        self._translation_binder.register(self._set_status_connecting, self._status_connecting)
        self._translation_binder.register(self._set_status_connected, self._status_connected)
        self._translation_binder.register(self._set_status_failed, self._status_failed)
        self._translation_binder.register(
            self._set_queue_label_template, self._queue_label_template
        )
        self._translation_binder.register(self._set_connect_text, self._connect_text)
        self._translation_binder.register(self._set_disconnect_text, self._disconnect_text)

        self.init_ui()
        self.load_settings()
        self._subscribe_language_events()

    def init_ui(self) -> None:  # noqa: PLR0915
        """Initialize the UI components."""
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(4)

        # Status indicator (moved to dock title when available)
        self.status_indicator = QLabel(self)
        self.status_indicator.setVisible(False)
        self.update_status_indicator(False)

        self.status_label = QLabel(self._status_disconnected, self)
        self.status_label.setStyleSheet("font-weight: bold;")
        self.status_label.setVisible(False)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        address_text = self._ts("Server")
        address_label = QLabel(address_text, self)
        self._translation_binder.register(address_label.setText, address_text)
        row.addWidget(address_label)

        self.server_combo = QComboBox(self)
        self.server_combo.setMinimumWidth(260)
        self.server_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.server_combo.setToolTip(self._ts("Choose a ComfyUI server configured in Preferences."))
        row.addWidget(self.server_combo)

        # Connect button
        connect_text = self._connect_text
        self.connect_button = QPushButton(connect_text, self)
        self._translation_binder.register(self.connect_button.setText, connect_text)
        self.connect_button.clicked.connect(self.toggle_connection)
        row.addWidget(self.connect_button)

        # Refresh button
        self.refresh_button = QPushButton("↻", self)
        self.refresh_button.setFixedSize(26, 26)
        refresh_tip = self._ts("Refresh object info")
        self.refresh_button.setToolTip(refresh_tip)
        self._translation_binder.register(self.refresh_button.setToolTip, refresh_tip)
        self.refresh_button.setEnabled(False)
        self.refresh_button.setVisible(False)
        self.refresh_button.clicked.connect(self.refresh_object_info)
        row.addWidget(self.refresh_button)
        row.addStretch(1)
        layout.addLayout(row)

        # Info labels (kept for updates, hidden in compact layout)
        self.queue_label = QLabel(self._queue_label_template.format(count=0), self)
        self.queue_label.setStyleSheet("color: gray;")
        self.queue_label.setVisible(False)

        self.version_label = QLabel("", self)
        self.version_label.setStyleSheet("color: gray; font-size: 10px;")
        self.version_label.setVisible(False)

        self.setLayout(layout)

        # Auto-reconnect timer
        self.reconnect_timer = QTimer()
        self.reconnect_timer.timeout.connect(self.auto_reconnect)

    def _set_status_disconnected(self, text: str) -> None:
        self._status_disconnected = text

    def _set_status_connecting(self, text: str) -> None:
        self._status_connecting = text

    def _set_status_connected(self, text: str) -> None:
        self._status_connected = text

    def _set_status_failed(self, text: str) -> None:
        self._status_failed = text

    def _set_queue_label_template(self, text: str) -> None:
        self._queue_label_template = text

    def _set_connect_text(self, text: str) -> None:
        self._connect_text = text

    def _set_disconnect_text(self, text: str) -> None:
        self._disconnect_text = text

    def _apply_connect_button_text(self) -> None:
        if self._connected:
            self.connect_button.setText(self._disconnect_text)
        else:
            self.connect_button.setText(self._connect_text)

    def _subscribe_language_events(self) -> None:
        handler = self._on_language_changed
        self._event_handlers.append(handler)
        self._event_tokens.append(self._event_bus.on(Events.LANGUAGE_CHANGED, handler))
        settings_handler = self._on_settings_changed
        self._event_handlers.append(settings_handler)
        self._event_tokens.append(self._event_bus.on(Events.SETTINGS_CHANGED, settings_handler))

    def _on_language_changed(self, _payload: Any) -> None:
        self.refresh_translations()

    def _on_settings_changed(self, payload: SettingsChangeEvent | Any) -> None:
        if not isinstance(payload, SettingsChangeEvent):
            return
        if payload.key.startswith("comfyui."):
            self.load_settings()
            return
        if payload.key != "privacy/allow_external_services":
            return
        if not bool(payload.new_value):
            logger.info("External services consent disabled; disconnecting ComfyUI")
            self.disconnect()

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()
        self._apply_status_text()
        self._update_queue_label()
        self._apply_connect_button_text()

    def _apply_status_text(self) -> None:
        if self._connecting:
            self.status_label.setText(self._status_connecting)
        elif self._connected:
            self.status_label.setText(self._status_connected)
        else:
            self.status_label.setText(self._status_disconnected)

    def _update_queue_label(self) -> None:
        self.queue_label.setText(self._queue_label_template.format(count=self._queue_count_value))

    def _ts(self, text: str) -> str:
        return translated_literal_domain("ConnectionBar", text, use_fallback=True)

    def _current_server_id(self) -> str:
        data = self.server_combo.currentData()
        return str(data) if data else "local"

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def _create_separator(self) -> QWidget:
        """Create a vertical separator."""
        sep = QLabel("|")
        sep.setStyleSheet("color: #ccc; padding: 0 5px;")
        return sep

    def update_status_indicator(self, connected: bool) -> None:
        """Update the status indicator dot.

        Args:
            connected: Connection status
        """
        # Create a colored circle indicator
        size = 12
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        color = QColor("#4CAF50") if connected else QColor("#F44336")
        if self._connecting:
            color = QColor("#FF9800")  # Orange for connecting

        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(1, 1, size - 2, size - 2)
        painter.end()

        self.status_indicator.setPixmap(pixmap)

    def load_settings(self) -> None:
        """Load the Preferences-managed server list."""
        current_id = self.server_combo.currentData()
        settings = getattr(self.service, "_settings", None) if self.service else None
        registry = sync_registry_from_settings(settings)
        self.server_combo.blockSignals(True)
        try:
            self.server_combo.clear()
            for server in registry.list_all():
                self.server_combo.addItem(f"{server.name} — {server.url}", server.id)
            if self.server_combo.count() == 0:
                self.server_combo.addItem(
                    self._ts("Local — {url}").format(url=DEFAULT_LOCAL_COMFYUI_URL), "local"
                )
            index = self.server_combo.findData(current_id)
            if index < 0:
                index = self.server_combo.findData(registry.default_server_id or "local")
            self.server_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.server_combo.blockSignals(False)

    def save_settings(self) -> None:
        """Emit the currently selected server; URLs are saved in Preferences."""
        server_id = self._current_server_id()
        server = get_registry().get(server_id) if server_id else None
        self.settings_changed.emit(
            {
                "server_id": server_id,
                "base_url": server.url if server is not None else "",
            }
        )

    def toggle_connection(self) -> None:
        """Toggle connection state."""
        if self._connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self) -> None:
        """Connect to ComfyUI server."""
        if self._connecting:
            return

        if not self._is_external_services_allowed():
            QMessageBox.warning(
                self,
                self._ts("External access disabled"),
                self._ts(
                    "Enable 'Allow external AI services' in Preferences to connect to ComfyUI."
                ),
            )
            return

        self._connecting = True
        self.connect_button.setEnabled(False)
        self.status_label.setText(self._status_connecting)
        self.update_status_indicator(False)

        self.save_settings()
        server_id = self._current_server_id()

        # Connect asynchronously
        import asyncio  # noqa: PLC0415

        async def do_connect():
            try:
                if self.service:
                    if not server_id:
                        raise ValueError("No ComfyUI server selected")

                    registry = sync_registry_from_settings(getattr(self.service, "_settings", None))
                    connection = await registry.get_or_create_connection(server_id)
                    self.service._connection = connection
                    await self.service.connect()

                    # Check health
                    health = await self.service.health()
                    if health:
                        self._connected = True
                        self._connecting = False

                        # Update UI
                        self.status_label.setText(self._status_connected)
                        self.connect_button.setText(self._disconnect_text)
                        self.connect_button.setEnabled(True)
                        self.refresh_button.setEnabled(True)
                        self.update_status_indicator(True)

                        # Disable inputs
                        self.server_combo.setEnabled(False)

                        # Get server info
                        await self.fetch_server_info()

                        # Fetch and cache object_info immediately after connection
                        logger.info("Fetching object_info for node schemas...")
                        await self.service.get_object_info(force_refresh=True)
                        logger.info("Object info cached successfully")

                        # Emit signal
                        self.connection_changed.emit(True)

                        logger.info(
                            "Connected to ComfyUI at %s",
                            getattr(connection, "base_url", server_id),
                        )
                    else:
                        raise Exception("Server health check failed")

            except Exception as e:
                logger.error(f"Connection failed: {e}")
                self._connecting = False
                self._connected = False

                self.status_label.setText(self._status_failed)
                self.connect_button.setText(self._connect_text)
                self.connect_button.setEnabled(True)
                self.update_status_indicator(False)

                # Don't start auto-reconnect - let user manually reconnect
                # self.reconnect_timer.start(5000)  # Disabled - manual reconnect only

        # Run in event loop
        # Use QTimer to schedule async task in qasync-compatible way
        from PySide6.QtCore import QTimer  # noqa: PLC0415

        def schedule_connect():
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(do_connect())  # noqa: RUF006
            except RuntimeError:
                logger.warning("No event loop available for connection")

        QTimer.singleShot(0, schedule_connect)

    def _is_external_services_allowed(self) -> bool:
        settings = getattr(self.service, "_settings", self.service)
        return is_external_services_allowed(settings)

    def disconnect(self) -> None:
        """Disconnect from ComfyUI server."""
        self._connected = False
        self._connecting = False
        self.reconnect_timer.stop()

        # Update UI
        self.status_label.setText(self._status_disconnected)
        self.connect_button.setText(self._connect_text)
        self.refresh_button.setEnabled(False)
        self.update_status_indicator(False)

        # Enable inputs
        self.server_combo.setEnabled(True)

        # Clear info
        self._queue_count_value = 0
        self.queue_label.setText(self._queue_label_template.format(count=0))
        self.version_label.setText("")
        self._teardown_connection()

        # Emit signal
        self.connection_changed.emit(False)

        logger.info("Disconnected from ComfyUI")

    def _teardown_connection(self) -> None:
        """Disconnect low-level connection asynchronously."""
        if not self.service or not self.service._connection:
            if self.service:
                self.service._connection = None
            return

        import asyncio  # noqa: PLC0415

        async def teardown() -> None:
            try:
                await self.service._connection.disconnect()  # type: ignore[union-attr]  # runtime-guarded None access
            except Exception as e:
                logger.warning("Failed to teardown ComfyUI connection: %s", e)
            finally:
                if self.service:
                    self.service._connection = None

        def schedule() -> None:
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(teardown())  # noqa: RUF006
            except RuntimeError:
                logger.warning("No event loop available for disconnect")
                if self.service:
                    self.service._connection = None

        QTimer.singleShot(0, schedule)

    def auto_reconnect(self) -> None:
        """Attempt automatic reconnection."""
        if not self._connected and not self._connecting:
            logger.debug("Attempting auto-reconnect...")
            self.connect()

    def refresh_object_info(self) -> None:
        """Refresh object info from server."""
        if not self._connected or not self.service:
            return
        if not self._is_external_services_allowed():
            logger.debug("Skipping object info refresh because external services are disabled")
            return

        import asyncio  # noqa: PLC0415

        async def refresh():
            try:
                await self.service.get_object_info(force_refresh=True)
                logger.info("Object info refreshed")
            except Exception as e:
                logger.error(f"Failed to refresh object info: {e}")

        # Use QTimer to schedule async task in qasync-compatible way
        from PySide6.QtCore import QTimer  # noqa: PLC0415

        def schedule_refresh():
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(refresh())  # noqa: RUF006
            except RuntimeError:
                logger.warning("No event loop available for refresh")

        QTimer.singleShot(0, schedule_refresh)

    async def fetch_server_info(self) -> None:
        """Fetch server information."""
        if not self._is_external_services_allowed():
            logger.debug("Skipping server info fetch because external services are disabled")
            return
        if not self.service or not self.service._connection:
            return

        try:
            # Get system stats
            stats = await self.service._connection.get_system_stats()
            if stats:
                # Update queue info
                queue_info = stats.get("exec_info", {}).get("queue_remaining", 0)
                self._queue_count_value = queue_info
                self.queue_label.setText(self._queue_label_template.format(count=queue_info))

            # Get version info (if available)
            # This would need to be implemented in the connection class

        except Exception as e:
            logger.error(f"Failed to fetch server info: {e}")

    def is_connected(self) -> bool:
        """Check if connected.

        Returns:
            True if connected to server
        """
        return self._connected

    def _format_address(self, host: str | None, port: int | None) -> str:
        if not host:
            return ""
        if port:
            return f"{host}:{port}"
        return host

    def _base_url_from_parts(self, host: str, port: int, use_ssl: bool) -> str:
        if not host or not port:
            return ""
        scheme = "https" if use_ssl else "http"
        return f"{scheme}://{host}:{port}"

    def _sync_local_server_registry(self, base_url: str) -> None:
        if not base_url:
            return
        try:
            from ..server_registry import ComfyUIServer, get_registry  # noqa: PLC0415

            registry = get_registry()
            existing = registry.get("local")
            registry.add(
                ComfyUIServer(
                    id="local",
                    name=existing.name if existing is not None else "Local",
                    url=base_url,
                    auto_detect=existing.auto_detect if existing is not None else True,
                    is_default=True,
                    color_hint=existing.color_hint if existing is not None else "#888888",
                )
            )
            registry.set_default("local")
        except Exception as exc:  # noqa: BLE001 - UI setting save must not crash on registry drift
            logger.warning("Failed to sync ComfyUI local server registry: %s", exc)

    def _parse_address(self, address: str) -> tuple[str, int]:
        cleaned = address.strip()
        if not cleaned:
            return "", 0
        if "://" in cleaned:
            _, _, cleaned = cleaned.partition("://")
        cleaned = cleaned.strip().rstrip("/")
        if cleaned.startswith("["):
            end = cleaned.find("]")
            if end != -1:
                host = cleaned[: end + 1]
                remainder = cleaned[end + 1 :]
                if remainder.startswith(":") and remainder[1:].isdigit():
                    return host, int(remainder[1:])
                return host, DEFAULT_COMFYUI_PORT
        if ":" in cleaned:
            host_part, port_part = cleaned.rsplit(":", 1)
            if port_part.isdigit():
                return host_part, int(port_part)
        return cleaned, DEFAULT_COMFYUI_PORT
