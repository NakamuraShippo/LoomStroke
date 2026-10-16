"""
Async WebSocket client with reconnection support.

Per Master.md PR-C specifications, provides WebSocket
functionality with automatic reconnection.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, cast

import aiohttp
from aiohttp import ClientSession

from loomstroke.platform.settings_manager import get_settings_manager
from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent

logger = logging.getLogger(__name__)


class AsyncWsClient:
    """
    Async WebSocket client with reconnection support.

    Per PR-C specifications, handles WebSocket connections.
    """

    def __init__(self, url: str, reconnect_interval: float = 5.0, heartbeat: float = 30.0):
        """
        Initialize WebSocket client.

        Args:
            url: WebSocket URL
            reconnect_interval: Reconnection interval in seconds
            heartbeat: Heartbeat interval in seconds
        """
        self.url = url
        self.reconnect_interval = reconnect_interval
        self.heartbeat = heartbeat
        self._session: ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._running = False
        self._reconnect_task: asyncio.Task[None] | None = None
        self._settings = get_settings_manager()

    def _ensure_external_services_allowed(self) -> None:
        if not ensure_network_services_consent(self._settings, service_name="External API"):
            raise RuntimeError(
                "External service access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable."
            )

    async def start(self) -> None:
        """Start WebSocket connection."""
        self._ensure_external_services_allowed()

        if not self._session:
            self._session = ClientSession()

        self._running = True
        await self._connect()

    async def _connect(self) -> None:
        """Connect to WebSocket."""
        session = self._session
        if session is None:
            raise RuntimeError("WebSocket client session was not started")

        try:
            self._ws = await session.ws_connect(self.url, heartbeat=self.heartbeat)
            logger.info(f"WebSocket connected to {self.url}")

        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            if self._running:
                await self._schedule_reconnect()

    async def _schedule_reconnect(self) -> None:
        """Schedule reconnection attempt."""
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        """Reconnect after delay."""
        await asyncio.sleep(self.reconnect_interval)

        if self._running:
            logger.info("Attempting WebSocket reconnection...")
            await self._connect()

    async def stop(self) -> None:
        """Stop WebSocket connection."""
        self._running = False

        if self._reconnect_task:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._session:
            await self._session.close()
            self._session = None

    async def send(self, data: Any) -> None:
        """
        Send data over WebSocket.

        Args:
            data: Data to send (will be JSON encoded)
        """
        self._ensure_external_services_allowed()
        if not self._ws:
            raise ConnectionError("WebSocket not connected")

        if isinstance(data, (dict, list)):
            await self._ws.send_json(data)
        else:
            await self._ws.send_str(str(data))

    async def send_json(self, data: dict[str, Any]) -> None:
        """
        Send JSON data over WebSocket.

        Args:
            data: Dictionary to send
        """
        self._ensure_external_services_allowed()
        await self.send(data)

    async def receive(self) -> dict[str, Any]:
        """
        Receive data from WebSocket.

        Returns:
            Received data

        Raises:
            ConnectionError: If not connected
        """
        self._ensure_external_services_allowed()
        if not self._ws:
            raise ConnectionError("WebSocket not connected")

        msg = await self._ws.receive()

        if msg.type == aiohttp.WSMsgType.TEXT:
            return cast("dict[str, Any]", json.loads(msg.data))
        elif msg.type == aiohttp.WSMsgType.BINARY:
            return {"binary": msg.data}
        elif msg.type == aiohttp.WSMsgType.ERROR:
            raise ConnectionError(f"WebSocket error: {self._ws.exception()}")
        elif msg.type == aiohttp.WSMsgType.CLOSE:
            if self._running:
                await self._schedule_reconnect()
            raise ConnectionError("WebSocket closed")
        else:
            raise ValueError(f"Unexpected message type: {msg.type}")

    async def __aenter__(self) -> AsyncWsClient:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.stop()

    @property
    def connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._ws is not None and not self._ws.closed
