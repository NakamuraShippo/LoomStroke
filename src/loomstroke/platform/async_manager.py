"""
Async manager for Qt/asyncio integration using qasync.

Per Master.md PR-C specifications, provides unified async/await support
with aiohttp, replacing all synchronous I/O operations.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import ClientSession, ClientTimeout, TCPConnector
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from loomstroke.platform.settings_manager import get_settings_manager
from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent

try:
    from qasync import QEventLoop, asyncSlot
except ImportError:
    raise ImportError("qasync is required. Install with: pip install qasync")  # noqa: B904


logger = logging.getLogger(__name__)

_HTTP_ERROR_STATUS = 400
_HTTP_SERVER_ERROR_STATUS = 500
# Enough to keep e.g. ComfyUI /prompt node_errors readable without flooding logs.
_ERROR_BODY_MAX_CHARS = 2000


class ExternalServiceDeniedError(PermissionError):
    """Raised when external service access is denied by user policy."""


class AsyncManager(QObject):
    """
    Manages Qt/asyncio integration and aiohttp sessions.

    This replaces all requests usage with aiohttp and ensures
    proper async operation within Qt event loop.
    """

    # Signals for async operations
    connection_established = Signal(str)
    connection_failed = Signal(str)
    request_started = Signal(str)
    request_completed = Signal(str, int)  # url, status_code
    request_failed = Signal(str, str)  # url, error

    def __init__(self, parent: QObject | None = None):
        """Initialize async manager."""
        super().__init__(parent)
        self.loop: QEventLoop | None = None
        self.session: ClientSession | None = None
        self._app: QApplication | None = None
        self._initialized = False
        self._settings = get_settings_manager()

        # Configuration
        self.timeout = ClientTimeout(total=30, connect=5, sock_read=10)
        self.max_connections = 10
        self.max_retries = 3
        self.backoff_factor = 2.0

    def _ensure_external_services_allowed(self) -> None:
        if not ensure_network_services_consent(self._settings, service_name="AsyncManager"):
            raise ExternalServiceDeniedError(
                "External service access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable."
            )

    async def initialize(self, app: QApplication) -> None:
        """
        Initialize Qt/asyncio integration.

        Args:
            app: Qt application instance
        """
        if self._initialized:
            logger.warning("AsyncManager already initialized")
            return

        self._app = app
        try:
            existing_loop = asyncio.get_event_loop()
        except RuntimeError:
            existing_loop = None

        if existing_loop and isinstance(existing_loop, QEventLoop):
            self.loop = existing_loop
            asyncio.set_event_loop(self.loop)
            logger.debug("Reusing existing qasync event loop")
        else:
            self.loop = QEventLoop(app)
            asyncio.set_event_loop(self.loop)
            logger.info("AsyncManager created new qasync event loop")

        # Create aiohttp session
        connector = TCPConnector(
            limit=self.max_connections, force_close=True, enable_cleanup_closed=True
        )

        self.session = ClientSession(
            timeout=self.timeout,
            connector=connector,
            headers={"User-Agent": "LoomStroke/2.0", "Accept": "application/json"},
        )

        self._initialized = True
        logger.info("AsyncManager initialized with qasync")

    async def cleanup(self) -> None:
        """Clean up resources."""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("Closed aiohttp session")

        self._initialized = False

    @asynccontextmanager
    async def managed_session(self) -> AsyncIterator[ClientSession]:
        """
        Context manager for session lifecycle.

        Yields:
            ClientSession: Managed aiohttp session
        """
        if not self.session:
            raise RuntimeError("AsyncManager not initialized")

        try:
            yield self.session
        finally:
            # Session cleanup is handled in cleanup()
            pass

    async def get(self, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        """
        Async GET request with retry logic.

        Args:
            url: Target URL
            **kwargs: Additional arguments for aiohttp

        Returns:
            Response object

        Raises:
            aiohttp.ClientError: On request failure
        """
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        """
        Async POST request with retry logic.

        Args:
            url: Target URL
            **kwargs: Additional arguments for aiohttp

        Returns:
            Response object

        Raises:
            aiohttp.ClientError: On request failure
        """
        return await self._request("POST", url, **kwargs)

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        """
        Send GET request and return JSON response.
        Response is consumed within context manager to avoid connection issues.

        Args:
            url: Target URL
            **kwargs: Additional arguments for aiohttp

        Returns:
            Parsed JSON response

        Raises:
            aiohttp.ClientError: On request failure
        """
        self._ensure_external_services_allowed()
        if not self.session:
            raise RuntimeError("AsyncManager not initialized")

        async with self.session.get(url, **kwargs) as resp:
            resp.raise_for_status()
            self.request_completed.emit(url, resp.status)
            return await resp.json()

    async def head_ok(self, url: str, **kwargs: Any) -> bool:
        """
        Check if URL is reachable using HEAD request.
        Returns True if status is 2xx or 3xx.

        Args:
            url: Target URL
            **kwargs: Additional arguments for aiohttp

        Returns:
            True if URL is reachable, False otherwise
        """
        self._ensure_external_services_allowed()
        if not self.session:
            return False

        try:
            async with self.session.head(url, **kwargs) as resp:
                return 200 <= resp.status < 400
        except Exception:
            return False

    async def _request(self, method: str, url: str, **kwargs: Any) -> aiohttp.ClientResponse:
        """
        Internal request method with retry logic.

        Implements exponential backoff per Master.md. Retries are limited to
        transient failures (connection errors, 5xx): a 4xx response is
        deterministic, so resending the identical request cannot succeed.
        """
        self._ensure_external_services_allowed()
        if not self.session:
            raise RuntimeError("AsyncManager not initialized")

        self.request_started.emit(url)

        error_msg = "Unknown error"
        attempts = 0
        for attempt in range(self.max_retries):
            attempts = attempt + 1
            response: aiohttp.ClientResponse | None = None
            try:
                # Exponential backoff with jitter
                if attempt > 0:
                    delay = self.backoff_factor**attempt
                    jitter = delay * 0.1  # 10% jitter
                    await asyncio.sleep(delay + jitter)
                    logger.debug(f"Retry {attempt + 1}/{self.max_retries} for {url}")

                response = await self.session.request(method, url, **kwargs)
                if response.status < _HTTP_ERROR_STATUS:
                    self.request_completed.emit(url, response.status)
                    return response

                # Read the body BEFORE releasing the connection —
                # raise_for_status() would discard it, and e.g. ComfyUI's
                # /prompt 400 carries node_errors identifying the bad input.
                error_msg = await self._http_error_message(response)
                response.release()
                if response.status < _HTTP_SERVER_ERROR_STATUS:
                    logger.warning(f"Request failed (no retry for {response.status}): {error_msg}")
                    break
                logger.warning(f"Request failed (attempt {attempt + 1}): {error_msg}")

            except ExternalServiceDeniedError:
                raise

            except aiohttp.ClientError as e:
                error_msg = str(e)
                logger.warning(f"Request failed (attempt {attempt + 1}): {e}")
                if response is not None and not response.closed:
                    response.close()

            except Exception as e:
                error_msg = str(e)
                logger.error(f"Unexpected error in request: {e}")
                if response is not None and not response.closed:
                    response.close()
                break

        self.request_failed.emit(url, error_msg)
        raise aiohttp.ClientError(f"Request failed after {attempts} attempts: {error_msg}")

    @staticmethod
    async def _http_error_message(response: aiohttp.ClientResponse) -> str:
        """Format an HTTP error like aiohttp does, plus the response body."""
        message = f"{response.status}, message={response.reason!r}, url={response.url!r}"
        try:
            body = (await response.text()).strip()
        except Exception:
            body = ""
        if body:
            message = f"{message}, body={body[:_ERROR_BODY_MAX_CHARS]}"
        return message

    async def upload_file(
        self, url: str, file_path: str, field_name: str = "file", **kwargs: Any
    ) -> aiohttp.ClientResponse:
        """
        Upload file using multipart/form-data.

        Migrated from legacy requests to aiohttp (multipart handled by helper).

        Args:
            url: Upload endpoint
            file_path: Path to file
            field_name: Form field name
            **kwargs: Additional form data

        Returns:
            Response object
        """
        data = aiohttp.FormData()

        # Read file content upfront so the handle is not closed before POST
        file_bytes = Path(file_path).read_bytes()
        data.add_field(
            field_name,
            file_bytes,
            filename=file_path.split("/")[-1],
            content_type="application/octet-stream",
        )

        # Add additional fields
        for key, value in kwargs.items():
            data.add_field(key, str(value))

        return await self.post(url, data=data)

    async def websocket_connect(self, url: str, **kwargs: Any) -> aiohttp.ClientWebSocketResponse:
        """
        Establish WebSocket connection.

        Args:
            url: WebSocket URL (ws:// or wss://)
            **kwargs: Additional arguments

        Returns:
            WebSocket response object
        """
        self._ensure_external_services_allowed()
        if not self.session:
            raise RuntimeError("AsyncManager not initialized")

        try:
            ws = await self.session.ws_connect(url, **kwargs)
            self.connection_established.emit(url)
            return ws
        except Exception as e:
            self.connection_failed.emit(str(e))
            raise

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def test_connection(self, url: str) -> bool:
        """
        Test connection to a URL.

        Args:
            url: URL to test

        Returns:
            True if connection successful
        """
        if not self.session:
            return False
        if not ensure_network_services_consent(self._settings, service_name="AsyncManager"):
            return False
        try:
            async with self.session.get(url) as response:
                return response.status == 200
        except Exception:
            return False

    def run_async(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Future[Any]:
        """
        Run async coroutine in Qt event loop.

        Args:
            coro: Coroutine to run

        Returns:
            Task object
        """
        if not self.loop:
            raise RuntimeError("AsyncManager not initialized")

        return asyncio.ensure_future(coro, loop=self.loop)


# Global instance
_async_manager: AsyncManager | None = None


def get_async_manager() -> AsyncManager:
    """
    Get global AsyncManager instance.

    Returns:
        AsyncManager instance
    """
    global _async_manager  # noqa: PLW0603
    if _async_manager is None:
        _async_manager = AsyncManager()
    return _async_manager


async def initialize_async(app: QApplication) -> AsyncManager:
    """
    Initialize and return async manager.

    Args:
        app: Qt application

    Returns:
        Initialized AsyncManager
    """
    manager = get_async_manager()
    await manager.initialize(app)
    return manager
