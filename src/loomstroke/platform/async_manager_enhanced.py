"""
Enhanced async manager with complete qasync integration.

Per Master.md PR#4 specifications, provides centralized session management
with proper lifecycle, cancellation, and timeout handling.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypeVar, cast

import aiohttp
from aiohttp import ClientSession, ClientTimeout, TCPConnector
from PySide6.QtCore import Signal

from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent

from .qt_async_integration import AsyncQObject, async_slot, get_qt_async_integration
from .retry_utils import with_retry
from .settings_manager import get_settings_manager

logger = logging.getLogger(__name__)

T = TypeVar("T")


class EnhancedAsyncManager(AsyncQObject):
    """
    Enhanced async manager with complete qasync integration.

    Centralizes all async operations with proper lifecycle management.
    """

    # Connection signals
    connection_established = Signal(str)
    connection_failed = Signal(str)
    connection_closed = Signal(str)

    # Request signals
    request_started = Signal(str, str)  # method, url
    request_completed = Signal(str, str, int)  # method, url, status
    request_failed = Signal(str, str, str)  # method, url, error
    request_progress = Signal(str, int, int)  # url, current, total

    # Session signals
    session_created = Signal()
    session_closed = Signal()
    session_error = Signal(str)

    def __init__(self, parent: AsyncQObject | None = None):
        """Initialize enhanced async manager."""
        super().__init__(parent)

        # Core components
        self._integration = get_qt_async_integration()
        self._settings = get_settings_manager()

        # Session management
        self._session: ClientSession | None = None
        self._connector: TCPConnector | None = None
        self._websockets: dict[str, aiohttp.ClientWebSocketResponse] = {}

        # Task tracking
        self._active_requests: dict[str, asyncio.Task[Any]] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()

        # Configuration from settings
        self._load_configuration()

        logger.info("EnhancedAsyncManager initialized")

    def _load_configuration(self) -> None:
        """Load configuration from settings."""
        self.timeout_seconds = self._settings.get_int("connection/timeout_s", 15)
        self.max_retries = self._settings.get_int("connection/max_retries", 3)
        self.backoff_factor = self._settings.get_float("connection/backoff_factor", 2.0)
        self.max_connections = self._settings.get_int("connection/max_connections", 10)
        self.verify_ssl = self._settings.get_bool("connection/verify_tls", True)

    def _ensure_external_services_allowed(self) -> None:
        if not ensure_network_services_consent(self._settings, service_name="EnhancedAsyncManager"):
            raise PermissionError(
                "External service access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable."
            )

    @async_slot()
    async def async_initialize(self) -> None:
        """
        Async initialization.

        Creates global session and sets up monitoring.
        """
        try:
            await self._create_session()
            self.async_ready.emit()
            logger.info("Async initialization complete")
        except Exception as e:
            error_msg = f"Failed to initialize: {e}"
            logger.error(error_msg)
            self.async_error.emit(error_msg)

    async def _create_session(self) -> None:
        """Create and configure aiohttp session."""
        if self._session and not self._session.closed:
            logger.warning("Session already exists")
            return

        # Create connector with connection pooling
        self._connector = TCPConnector(
            limit=self.max_connections,
            limit_per_host=5,
            force_close=True,
            enable_cleanup_closed=True,
            ssl=self.verify_ssl,
        )

        # Create session with timeout
        timeout = ClientTimeout(total=self.timeout_seconds, connect=5, sock_read=10)

        self._session = ClientSession(
            connector=self._connector,
            timeout=timeout,
            headers={"User-Agent": "LoomStroke/2.0", "Accept": "application/json"},
        )

        self.session_created.emit()
        logger.info(f"Session created with {self.max_connections} max connections")

    @async_slot()
    async def async_cleanup(self) -> None:
        """Clean up all resources."""
        logger.info("Starting async cleanup...")

        # Cancel active requests
        for task in self._active_requests.values():
            if not task.done():
                task.cancel()

        if self._active_requests:
            await asyncio.gather(*self._active_requests.values(), return_exceptions=True)
        self._active_requests.clear()

        # Close websockets
        for ws in self._websockets.values():
            if not ws.closed:
                await ws.close()
        self._websockets.clear()

        # Close session
        if self._session and not self._session.closed:
            await self._session.close()
            self.session_closed.emit()

        # Cancel background tasks
        for task in self._background_tasks:
            if not task.done():
                task.cancel()

        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        # Parent cleanup
        await super().async_cleanup()

        logger.info("Async cleanup complete")

    @asynccontextmanager
    async def managed_request(self, method: str, url: str) -> AsyncIterator[None]:
        """
        Context manager for request lifecycle.

        Args:
            method: HTTP method
            url: Request URL

        Yields:
            Request context
        """
        self._ensure_external_services_allowed()
        request_id = f"{method}:{url}"
        self.request_started.emit(method, url)

        current_task = asyncio.current_task()
        if current_task is not None:
            self._active_requests[request_id] = current_task

        try:
            yield
        except Exception as e:
            self.request_failed.emit(method, url, str(e))
            raise
        finally:
            if request_id in self._active_requests:
                del self._active_requests[request_id]

    async def request(self, method: str, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        """
        Make HTTP request with retry and tracking.

        Args:
            method: HTTP method
            url: Request URL
            **kwargs: Additional arguments

        Returns:
            Response object
        """
        self._ensure_external_services_allowed()
        if self._session is None or self._session.closed:
            await self._create_session()

        session = self._session
        if session is None:
            raise RuntimeError("Failed to initialize aiohttp session")

        async with self.managed_request(method, url):

            async def make_request() -> aiohttp.ClientResponse:
                async with session.request(method, url, ssl=self.verify_ssl, **kwargs) as response:
                    response.raise_for_status()
                    self.request_completed.emit(method, url, response.status)
                    return response

            return await with_retry(
                make_request, retries=self.max_retries, base_delay=0.5, max_delay=30.0
            )

    @async_slot(str)
    async def get(self, url: str, **kwargs: Any) -> dict[str, Any]:
        """
        Make GET request.

        Args:
            url: Request URL
            **kwargs: Additional arguments

        Returns:
            JSON response
        """
        self._ensure_external_services_allowed()
        response = await self.request("GET", url, **kwargs)
        return cast(dict[str, Any], await response.json())

    @async_slot(str, dict)
    async def post(
        self, url: str, data: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """
        Make POST request.

        Args:
            url: Request URL
            data: JSON data
            **kwargs: Additional arguments

        Returns:
            JSON response
        """
        self._ensure_external_services_allowed()
        response = await self.request("POST", url, json=data, **kwargs)
        return cast(dict[str, Any], await response.json())

    @async_slot(str, str)
    async def upload_file(self, url: str, file_path: str, **kwargs: Any) -> dict[str, Any]:
        """
        Upload file with progress tracking.

        Args:
            url: Upload URL
            file_path: Path to file
            **kwargs: Additional form fields

        Returns:
            JSON response
        """
        self._ensure_external_services_allowed()
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Create form data
        data = aiohttp.FormData()

        with open(path, "rb") as f:
            data.add_field("file", f, filename=path.name, content_type="application/octet-stream")

            # Add additional fields
            for key, value in kwargs.items():
                data.add_field(key, str(value))

            # Upload with progress
            response = await self.request("POST", url, data=data)
            return cast(dict[str, Any], await response.json())

    @async_slot(str)
    async def connect_websocket(self, url: str) -> str:
        """
        Connect to WebSocket.

        Args:
            url: WebSocket URL

        Returns:
            Connection ID
        """
        self._ensure_external_services_allowed()
        if self._session is None or self._session.closed:
            await self._create_session()

        session = self._session
        if session is None:
            raise RuntimeError("Failed to initialize aiohttp session")

        try:
            ws = await session.ws_connect(url)
            conn_id = f"ws_{len(self._websockets)}"
            self._websockets[conn_id] = ws

            self.connection_established.emit(url)

            self.create_background_task(self._handle_websocket_messages(conn_id, ws))

            return conn_id

        except Exception as e:
            error_msg = f"WebSocket connection failed: {e}"
            self.connection_failed.emit(error_msg)
            raise

    async def _handle_websocket_messages(
        self, conn_id: str, ws: aiohttp.ClientWebSocketResponse
    ) -> None:
        """
        Handle incoming WebSocket messages.

        Args:
            conn_id: Connection ID
            ws: WebSocket connection
        """
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    # Emit message through event bus
                    pass
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    break
        finally:
            if conn_id in self._websockets:
                del self._websockets[conn_id]
            self.connection_closed.emit(conn_id)

    @async_slot(str)
    async def close_websocket(self, conn_id: str) -> None:
        """
        Close WebSocket connection.

        Args:
            conn_id: Connection ID
        """
        if conn_id in self._websockets:
            ws = self._websockets[conn_id]
            if not ws.closed:
                await ws.close()
            del self._websockets[conn_id]
            self.connection_closed.emit(conn_id)

    def create_background_task(self, coro: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        """Create and track a managed background task."""
        task = self.create_async_task(coro)

        def _cleanup(finished: asyncio.Future[Any]) -> None:
            self._background_tasks.discard(cast(asyncio.Task[Any], finished))

        self._background_tasks.add(task)
        task.add_done_callback(_cleanup)
        return task

    @property
    def session(self) -> ClientSession | None:
        """Get current session."""
        return self._session

    @property
    def is_initialized(self) -> bool:
        """Check if manager is initialized."""
        return self._session is not None and not self._session.closed


# Global instance
_enhanced_manager: EnhancedAsyncManager | None = None


def get_enhanced_async_manager() -> EnhancedAsyncManager:
    """
    Get global enhanced async manager.

    Returns:
        EnhancedAsyncManager singleton
    """
    manager = getattr(get_enhanced_async_manager, "_instance", None)
    if manager is None:
        manager = EnhancedAsyncManager()
        get_enhanced_async_manager._instance = manager  # type: ignore[attr-defined]  # function attribute as singleton storage
    return cast(EnhancedAsyncManager, manager)
