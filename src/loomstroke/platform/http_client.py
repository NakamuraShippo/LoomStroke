"""
Async HTTP client with retry support.

Per Master.md PR-C specifications, provides HTTP client
functionality using aiohttp with proper lifecycle management.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiohttp
from aiohttp import ClientSession, ClientTimeout

from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent

from .retry_utils import with_retry
from .settings_manager import get_settings_manager

logger = logging.getLogger(__name__)


def _ensure_external_services_allowed() -> None:
    if not ensure_network_services_consent(get_settings_manager(), service_name="External API"):
        raise RuntimeError(
            "External service access is disabled. "
            "Set 'privacy/allow_external_services' to true to enable."
        )


@asynccontextmanager
async def client_session(
    timeout_seconds: int = 15, connector_limit: int = 100, verify_ssl: bool = True
) -> AsyncIterator[ClientSession]:
    """
    Create an aiohttp ClientSession with proper lifecycle management.

    Args:
        timeout_seconds: Total timeout for requests
        connector_limit: Maximum number of connections
        verify_ssl: Whether to verify SSL certificates

    Yields:
        Configured ClientSession
    """
    _ensure_external_services_allowed()

    timeout = ClientTimeout(total=timeout_seconds)

    connector = aiohttp.TCPConnector(
        limit=connector_limit, force_close=True, enable_cleanup_closed=True
    )

    async with ClientSession(timeout=timeout, connector=connector, connector_owner=True) as session:
        yield session


class AsyncHttpClient:
    """
    Async HTTP client with automatic retry.

    Per PR-C specifications, replaces requests with aiohttp.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int = 15,
        retries: int = 3,
        verify_ssl: bool = True,
    ):
        """
        Initialize HTTP client.

        Args:
            base_url: Base URL for requests
            timeout: Request timeout in seconds
            retries: Number of retry attempts
            verify_ssl: Whether to verify SSL certificates
        """
        self.base_url = base_url
        self.timeout = timeout
        self.retries = retries
        self.verify_ssl = verify_ssl
        self._session: ClientSession | None = None

    async def start(self) -> None:
        """Start the client session."""
        if not self._session:
            _ensure_external_services_allowed()
            timeout = ClientTimeout(total=self.timeout)
            connector = aiohttp.TCPConnector(
                limit=100, force_close=True, enable_cleanup_closed=True
            )
            self._session = ClientSession(
                timeout=timeout, connector=connector, connector_owner=True
            )

    async def close(self) -> None:
        """Close the client session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> AsyncHttpClient:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    def _get_url(self, path: str) -> str:
        """
        Get full URL from path.

        Args:
            path: URL path or full URL

        Returns:
            Full URL
        """
        if path.startswith(("http://", "https://", "ws://", "wss://")):
            return path

        if self.base_url:
            return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

        return path

    async def get(
        self, path: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """
        Make GET request with retry.

        Args:
            path: URL path or full URL
            params: Query parameters
            headers: Request headers

        Returns:
            JSON response
        """
        _ensure_external_services_allowed()
        if not self._session:
            await self.start()
        session = self._session
        if session is None:
            raise RuntimeError("HTTP client session was not started")

        url = self._get_url(path)

        async def request() -> Any:
            async with session.get(
                url, params=params, headers=headers, ssl=self.verify_ssl
            ) as response:
                response.raise_for_status()
                return await response.json()

        return await with_retry(request, retries=self.retries)

    async def post(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        data: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Make POST request with retry.

        Args:
            path: URL path or full URL
            json: JSON data to send
            data: Form data to send
            headers: Request headers

        Returns:
            JSON response
        """
        _ensure_external_services_allowed()
        if not self._session:
            await self.start()
        session = self._session
        if session is None:
            raise RuntimeError("HTTP client session was not started")

        url = self._get_url(path)

        async def request() -> Any:
            async with session.post(
                url, json=json, data=data, headers=headers, ssl=self.verify_ssl
            ) as response:
                response.raise_for_status()
                return await response.json()

        return await with_retry(request, retries=self.retries)

    async def upload_image(
        self,
        path: str,
        image_data: bytes,
        filename: str = "image.png",
        additional_fields: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Upload image data.

        Args:
            path: Upload endpoint path
            image_data: Image bytes
            filename: Filename for upload
            additional_fields: Additional form fields

        Returns:
            JSON response
        """
        _ensure_external_services_allowed()
        if not self._session:
            await self.start()
        session = self._session
        if session is None:
            raise RuntimeError("HTTP client session was not started")

        url = self._get_url(path)

        async def request() -> Any:
            data = aiohttp.FormData()
            data.add_field("image", image_data, filename=filename, content_type="image/png")

            if additional_fields:
                for key, value in additional_fields.items():
                    data.add_field(key, value)

            async with session.post(url, data=data, ssl=self.verify_ssl) as response:
                response.raise_for_status()
                return await response.json()

        return await with_retry(request, retries=self.retries)

    async def health_check(self, path: str = "/health") -> bool:
        """
        Check if service is healthy.

        Args:
            path: Health check endpoint path

        Returns:
            True if healthy
        """
        _ensure_external_services_allowed()
        try:
            await self.get(path)
            return True
        except Exception as e:
            logger.debug(f"Health check failed: {e}")
            return False
