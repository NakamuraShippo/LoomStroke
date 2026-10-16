"""
Async ComfyUI connection using aiohttp.

Per Master.md PR-C specifications, uses unified async HTTP utilities
with timeout, retry, and backoff support.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
from PySide6.QtCore import QObject, Signal

from ...platform import AsyncManager
from ...platform.settings_schema import DEFAULT_COMFYUI_URL

logger = logging.getLogger(__name__)


@dataclass
class ConnectionStatus:
    """Connection status information."""

    connected: bool = False
    url: str = ""
    client_id: str = ""
    error: str | None = None


class AsyncComfyUIConnection(QObject):
    """
    Async connection manager for ComfyUI.

    Replaces requests-based implementation with aiohttp.
    """

    # Signals
    connected = Signal()
    disconnected = Signal()
    # Emitted when the WebSocket drops without an explicit ``disconnect()`` call
    # (server closed, transport error, or unexpected exception in the message
    # handler). The payload is a human-readable reason string. ``disconnected``
    # is also emitted so UI state reset logic continues to work unchanged.
    # Pre-release audit §12-C #5 / §12-N (2026-04-11).
    disconnected_unexpectedly = Signal(str)
    error_occurred = Signal(str)
    message_received = Signal(dict)
    progress_updated = Signal(str, int, int)  # job_id, current, total

    def __init__(
        self,
        async_manager: AsyncManager,
        base_url: str = DEFAULT_COMFYUI_URL,
        parent: QObject | None = None,
    ):
        """
        Initialize async connection.

        Args:
            async_manager: AsyncManager instance
            base_url: ComfyUI base URL
            parent: Parent QObject
        """
        super().__init__(parent)

        self.async_manager = async_manager
        self.base_url = base_url
        self.client_id = str(uuid.uuid4())
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.status = ConnectionStatus(url=base_url, client_id=self.client_id)

        # WebSocket task
        self._ws_task: asyncio.Task | None = None
        self._message_queue: asyncio.Queue[Any] | None = None

        # Set to True by ``disconnect()`` so that ``_handle_messages()`` can
        # distinguish an explicit teardown from an unexpected server drop
        # and avoid double-emitting ``disconnected_unexpectedly``.
        # Pre-release audit §12-C #5 / §12-N (2026-04-11).
        self._intentional_disconnect: bool = False
        self._unexpected_notified: bool = False

        # Cache for object_info obtained during connection
        self._cached_object_info: dict | None = None

        logger.info(f"AsyncComfyUIConnection initialized: {base_url}")

    async def connect(self) -> bool:
        """
        Connect to ComfyUI server.

        Returns:
            True if connection successful
        """
        # Reset disconnect tracking for a fresh session. Pre-release audit §12-N.
        self._intentional_disconnect = False
        self._unexpected_notified = False

        try:
            # Test HTTP connection - try /object_info first (more reliable)
            # This also serves to cache the object_info data
            test_url = f"{self.base_url}/object_info"
            object_info_data = None
            try:
                # Fully consume response to avoid connection issues
                object_info_data = await self.async_manager.get_json(test_url)
                if not object_info_data:
                    raise aiohttp.ClientError("Empty response from server")
                # Store for later caching
                self._cached_object_info = object_info_data
            except Exception:
                # Fallback to /queue as alternative health check
                test_url = f"{self.base_url}/queue"
                await self.async_manager.get_json(test_url)

            # Connect WebSocket
            ws_url = f"{self.base_url.replace('http', 'ws')}/ws?clientId={self.client_id}"
            self.ws = await self.async_manager.websocket_connect(ws_url)
            self._message_queue = asyncio.Queue(maxsize=4096)

            # Start message handler
            self._ws_task = asyncio.create_task(self._handle_messages())

            self.status.connected = True
            self.status.error = None
            self.connected.emit()

            logger.info(f"Connected to ComfyUI at {self.base_url}")
            return True

        except Exception as e:
            error_msg = f"Connection failed: {e}"
            logger.error(error_msg)
            self.status.connected = False
            self.status.error = str(e)
            self.error_occurred.emit(error_msg)
            return False

    async def disconnect(self) -> None:
        """Disconnect from ComfyUI server."""
        # Mark this teardown as intentional so that ``_handle_messages()``
        # does not treat the WSMsgType.CLOSED that follows as an unexpected
        # drop. Pre-release audit §12-N (2026-04-11).
        self._intentional_disconnect = True

        if self._ws_task:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
            self._ws_task = None

        if self.ws:
            await self.ws.close()
            self.ws = None

        self._message_queue = None
        self.status.connected = False
        self.disconnected.emit()
        logger.info("Disconnected from ComfyUI")

    async def _handle_messages(self) -> None:
        """Handle incoming WebSocket messages."""
        if not self.ws:
            return

        try:
            async for msg in self.ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        self._broadcast_ws_message(data)
                        self._process_message(data)
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse WebSocket message: {e}")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    error_msg = f"WebSocket error: {self.ws.exception()}"
                    logger.error(error_msg)
                    self.error_occurred.emit(error_msg)
                    # Mark connection as closed and notify listeners.
                    # Pre-release audit §12-N (2026-04-11).
                    self.status.connected = False
                    self._notify_unexpected_disconnect(error_msg)
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("WebSocket closed")
                    self.status.connected = False
                    # Pre-release audit §12-N (2026-04-11): previously this
                    # path was silent. Notify listeners unless the caller
                    # explicitly asked for a disconnect.
                    self._notify_unexpected_disconnect("WebSocket closed by server")
                    break
        except asyncio.CancelledError:
            # Task was cancelled, this is expected
            logger.debug("WebSocket handler cancelled")
            raise
        except Exception as e:
            logger.error(f"Error in message handler: {e}")
            self.error_occurred.emit(str(e))
            self.status.connected = False
            self._notify_unexpected_disconnect(f"WebSocket handler error: {e}")
        finally:
            # Ensure connection state is updated
            if self.ws and not self.ws.closed:
                await self.ws.close()
            self.ws = None
            self.status.connected = False

    def _notify_unexpected_disconnect(self, reason: str) -> None:
        """Emit disconnect signals for a server-initiated / error-induced drop.

        Invoked from ``_handle_messages()`` on the ERROR / CLOSED / except
        paths. No-op when the caller has already requested an explicit
        ``disconnect()`` (``_intentional_disconnect`` is True), or when a
        previous drop in the same session already notified listeners
        (``_unexpected_notified`` is True).

        Emits both ``disconnected_unexpectedly(reason)`` (new in §12-N, for
        UI notification) and ``disconnected()`` (existing, for UI state reset)
        so that consumers that only listened to ``disconnected`` continue to
        receive a reset signal without code changes.

        Pre-release audit §12-C #5 / §12-N (2026-04-11).
        """
        if self._intentional_disconnect or self._unexpected_notified:
            return
        self._unexpected_notified = True
        self.disconnected_unexpectedly.emit(reason)
        self.disconnected.emit()

    def _broadcast_ws_message(self, message: dict[str, Any]) -> None:
        """Push message into queue for stream consumers."""
        queue = self._message_queue
        if queue is None:
            return
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            # Drop oldest entry when queue is full.
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(message)

    @staticmethod
    def _extract_prompt_id(data: dict[str, Any]) -> str | None:
        """Extract prompt id from websocket message payload."""
        payload = data.get("data")
        if isinstance(payload, dict):
            prompt_id = payload.get("prompt_id")
            if prompt_id is not None:
                return str(prompt_id)

            nested = payload.get("data")
            if isinstance(nested, dict):
                nested_prompt = nested.get("prompt_id")
                if nested_prompt is not None:
                    return str(nested_prompt)

        top_level_prompt = data.get("prompt_id")
        if top_level_prompt is not None:
            return str(top_level_prompt)

        return None

    def _process_message(self, data: dict[str, Any]) -> None:
        """
        Process WebSocket message.

        Args:
            data: Message data
        """
        msg_type = data.get("type")

        if msg_type == "status":
            # Status update
            status = data.get("data", {}).get("status", {})
            exec_info = status.get("exec_info", {})

            if exec_info.get("queue_remaining", 0) > 0:
                logger.debug(f"Queue remaining: {exec_info['queue_remaining']}")

        elif msg_type == "progress":
            # Progress update
            progress_data = data.get("data", {})
            job_id = progress_data.get("prompt_id", "")
            value = progress_data.get("value", 0)
            max_value = progress_data.get("max", 100)

            self.progress_updated.emit(job_id, value, max_value)

        elif msg_type == "executing":
            # Node execution update
            node = data.get("data", {}).get("node")
            if node:
                logger.debug(f"Executing node: {node}")

        elif msg_type == "executed":
            # Execution completed
            output = data.get("data", {}).get("output", {})
            if output:
                logger.info("Execution completed")

        # Emit raw message for processing
        self.message_received.emit(data)

    async def queue_prompt(self, workflow: dict[str, Any], client_id: str | None = None) -> str:
        """
        Queue a workflow prompt.

        Args:
            workflow: Workflow definition
            client_id: Optional client ID (uses self.client_id if not provided)

        Returns:
            Prompt ID

        Raises:
            aiohttp.ClientError: On request failure
        """
        try:
            url = f"{self.base_url}/prompt"

            payload = {"prompt": workflow, "client_id": client_id or self.client_id}

            response = await self.async_manager.post(url, json=payload)
            try:
                if response.status == 200:
                    try:
                        # Add timeout for reading response
                        data = await asyncio.wait_for(response.json(), timeout=5.0)
                        prompt_id = data.get("prompt_id", "")
                        logger.info(f"Queued prompt: {prompt_id}")
                        return prompt_id
                    except TimeoutError as exc:
                        logger.error("Timeout reading response from ComfyUI")
                        raise aiohttp.ClientError("Timeout reading response from ComfyUI") from exc
                    except json.JSONDecodeError as e:
                        logger.error(f"Invalid JSON response: {e}")
                        raise aiohttp.ClientError(f"Invalid JSON response from ComfyUI: {e}") from e
                else:
                    try:
                        error_text = await asyncio.wait_for(response.text(), timeout=2.0)
                    except (TimeoutError, Exception):
                        error_text = f"HTTP {response.status}"
                    raise aiohttp.ClientError(
                        f"Failed to queue prompt: {response.status} - {error_text}"
                    )
            finally:
                # Ensure response is properly closed
                response.close()
        except Exception as e:
            logger.error(f"Failed to queue prompt: {e}")
            raise

    async def get_history(self, prompt_id: str) -> dict[str, Any]:
        """
        Get execution history for a prompt.

        Args:
            prompt_id: Prompt ID

        Returns:
            History data
        """
        try:
            url = f"{self.base_url}/history/{prompt_id}"
            return await self.async_manager.get_json(url)
        except Exception as e:
            logger.error(f"Failed to get history for {prompt_id}: {e}")
            return {}

    async def get_view_metadata(self, folder: str, filename: str) -> dict[str, Any]:
        """
        Get metadata for generated image.

        Args:
            folder: Output folder
            filename: Image filename

        Returns:
            Metadata dictionary
        """
        url = f"{self.base_url}/view_metadata/{folder}/{filename}"
        return await self.async_manager.get_json(url)

    async def upload_image(
        self, image_path: str, name: str | None = None, overwrite: bool = True
    ) -> dict[str, Any]:
        """
        Upload image to ComfyUI.

        Migrated from legacy requests to aiohttp (multipart handled by helper).

        Args:
            image_path: Path to image file
            name: Optional name for uploaded file
            overwrite: Whether to overwrite existing file

        Returns:
            Uploaded filename (relative to ComfyUI's input folder)
        """
        url = f"{self.base_url}/upload/image"

        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        # Prepare form data
        data = aiohttp.FormData()

        # Read file with fallback to thread pool if aiofiles not available
        try:
            import aiofiles  # type: ignore  # noqa: PLC0415

            async with aiofiles.open(image_path, "rb") as f:
                file_content = await f.read()
        except (ModuleNotFoundError, ImportError):
            # Fallback to thread pool executor
            loop = asyncio.get_running_loop()
            file_content = await loop.run_in_executor(None, path.read_bytes)

        data.add_field("image", file_content, filename=name or path.name, content_type="image/png")

        data.add_field("overwrite", str(overwrite).lower())

        response = await self.async_manager.post(url, data=data)
        result = await response.json()

        filename = result.get("filename") or result.get("name") or path.name
        subfolder = result.get("subfolder") or ""
        folder_type = result.get("type") or ""

        logger.info(
            "Uploaded image: %s (folder=%s, subfolder=%s)", filename, folder_type, subfolder
        )

        if subfolder:
            return f"{subfolder}/{filename}"
        return filename

    async def upload_image_bytes(self, png_bytes: bytes, filename: str, subfolder: str = "") -> str:
        """Upload raw bytes to ComfyUI's ``/upload/image`` endpoint.

        Same wire format as :meth:`upload_image` (``multipart/form-data``
        with ``image`` + ``overwrite`` fields, optional ``subfolder``) but
        accepts the payload directly as ``bytes`` — no disk read. Used by
        :class:`loomstroke.plugins.ai_effect.gateways.comfyui.ComfyUIGateway`
        when the slot source image is already in memory.

        Args:
            png_bytes: PNG-encoded image bytes.
            filename: Filename to register server-side. ComfyUI may rename
                on collision when ``overwrite`` is False; the echoed name
                is returned.
            subfolder: Optional nested folder *inside* ComfyUI's input
                directory. Defaults to ``""`` — the file is saved to the
                input/ root, matching what :meth:`upload_image` does when
                the ComfyUI panel uploads a layer snapshot. The previous
                default ``"input"`` caused the file to land at
                ``input/input/<filename>`` which LoadImage's INPUT_TYPES
                enum doesn't surface, so ComfyUI's strict validator would
                silently fall back to a different file (dogfood symptom:
                "入力レイヤーの内容をComfyUIに送信していない" — the content
                never reached the node because the path was wrong).

        Returns:
            The server-echoed filename, possibly prefixed with
            ``"{subfolder}/"`` when the server placed it in a nested folder.
        """
        url = f"{self.base_url}/upload/image"

        data = aiohttp.FormData()
        data.add_field("image", png_bytes, filename=filename, content_type="image/png")
        data.add_field("overwrite", "true")
        if subfolder:
            data.add_field("subfolder", subfolder)

        response = await self.async_manager.post(url, data=data)
        result = await response.json()

        echoed_filename = result.get("filename") or result.get("name") or filename
        echoed_subfolder = result.get("subfolder") or ""

        logger.info("Uploaded image bytes: %s (subfolder=%s)", echoed_filename, echoed_subfolder)

        if echoed_subfolder:
            return f"{echoed_subfolder}/{echoed_filename}"
        return echoed_filename

    async def get_image(
        self, filename: str, subfolder: str = "", folder_type: str = "output"
    ) -> bytes:
        """
        Get image from ComfyUI.

        Args:
            filename: Image filename
            subfolder: Subfolder path
            folder_type: Folder type (output, input, temp)

        Returns:
            Image bytes
        """
        params = {"filename": filename, "subfolder": subfolder, "type": folder_type}

        url = f"{self.base_url}/view"
        response = await self.async_manager.get(url, params=params)
        return await response.read()

    async def interrupt(self) -> None:
        """Interrupt current execution."""
        url = f"{self.base_url}/interrupt"
        await self.async_manager.post(url)
        logger.info("Execution interrupted")

    async def get_embeddings(self) -> list[str]:
        """
        Get available embeddings.

        Returns:
            List of embedding names
        """
        url = f"{self.base_url}/embeddings"
        return await self.async_manager.get_json(url)

    async def get_extensions(self) -> list[str]:
        """
        Get available extensions.

        Returns:
            List of extension names
        """
        url = f"{self.base_url}/extensions"
        return await self.async_manager.get_json(url)

    async def get_models(self, model_type: str | None = None) -> dict[str, list[str]]:
        """
        Get available models organized by type.

        Args:
            model_type: Optional specific model type to fetch

        Returns:
            Dictionary mapping model types to lists of model names
        """
        try:
            # Try the /models endpoint if it exists
            url = f"{self.base_url}/models"
            if model_type:
                url += f"?type={model_type}"
            return await self.async_manager.get_json(url)
        except Exception as e:
            logger.debug(f"Could not fetch models from /models endpoint: {e}")
            # Return empty dict as fallback
            return {}

    async def get_system_stats(self) -> dict[str, Any] | None:
        """
        Get system statistics from ComfyUI server.

        Returns:
            System stats dictionary or None if failed
        """
        try:
            url = f"{self.base_url}/system_stats"
            return await self.async_manager.get_json(url)
        except Exception as e:
            logger.error(f"Failed to get system stats: {e}")
            return None

    async def get_object_info(self) -> dict[str, Any] | None:
        """
        Get object info (nodes and models) from ComfyUI server.

        Returns:
            Object info dictionary with nodes and models or None if failed
        """
        # Return cached object_info if available (obtained during connect)
        if self._cached_object_info:
            logger.debug("Returning cached object_info from connection")
            cached = self._cached_object_info
            # Clear cache after first use to allow refresh if needed
            self._cached_object_info = None
            return cached

        try:
            url = f"{self.base_url}/object_info"
            return await self.async_manager.get_json(url)
        except Exception as e:
            logger.error(f"Failed to get object info: {e}")
            return None

    async def get_result(self, job_id: str) -> dict[str, Any] | None:
        """
        Get result for a job/prompt.

        Args:
            job_id: Job/Prompt ID

        Returns:
            Result dictionary or None if not ready/failed
        """
        try:
            history = await self.get_history(job_id)
            if history and job_id in history:
                return history[job_id]
            return None
        except Exception as e:
            logger.error(f"Failed to get result for {job_id}: {e}")
            return None

    async def stream_progress(self, job_id: str) -> AsyncIterator[dict[str, Any]]:
        """
        Stream progress updates for a job via WebSocket.

        Args:
            job_id: Job/Prompt ID to monitor

        Yields:
            Progress update dictionaries
        """
        if not self.ws:
            logger.warning(f"WebSocket not connected for streaming job {job_id}")
            return

        if self._message_queue is None:
            self._message_queue = asyncio.Queue(maxsize=4096)
        message_queue = self._message_queue

        try:
            while True:
                if not self.status.connected and message_queue.empty():
                    break

                try:
                    message = await asyncio.wait_for(message_queue.get(), timeout=0.25)
                except TimeoutError:
                    continue

                if not isinstance(message, dict):
                    continue

                msg_type = message.get("type")
                if not isinstance(msg_type, str):
                    continue

                prompt_id = self._extract_prompt_id(message)
                if prompt_id is not None and prompt_id != job_id:
                    continue

                if msg_type == "progress":
                    progress_data = message.get("data")
                    if isinstance(progress_data, dict):
                        yield {"type": "progress", **progress_data}
                    continue

                if msg_type in {"execution_success", "execution_complete", "complete"}:
                    exec_data = message.get("data")
                    if not isinstance(exec_data, dict):
                        exec_data = {}
                    yield {"type": "executed", "data": exec_data}
                    break

                # "executing" with node=null signals workflow completion
                # in older ComfyUI versions that don't send execution_success.
                if msg_type == "executing":
                    exec_data = message.get("data")
                    if isinstance(exec_data, dict) and exec_data.get("node") is None:
                        yield {"type": "executed", "data": exec_data}
                        break
                    continue

                # Per-node "executed" events are informational; do not break.
                if msg_type == "executed":
                    continue

                if msg_type in {"execution_error", "error"}:
                    error_data = message.get("data")
                    if not isinstance(error_data, dict):
                        error_data = {}
                    yield {"type": "error", "data": error_data}
                    break

            if not self.status.connected:
                logger.warning(f"WebSocket closed during progress streaming for {job_id}")
        except Exception as e:
            logger.error(f"Error streaming progress for {job_id}: {e}")

    def is_connected(self) -> bool:
        """Check if connected."""
        return self.status.connected

    def get_status(self) -> ConnectionStatus:
        """Get connection status."""
        return self.status


class AsyncConnectionManager(AsyncComfyUIConnection):
    """Backward-compatible connection manager shim.

    Older integrations construct ``AsyncConnectionManager`` directly;
    this subclass preserves that API while delegating to the new
    `AsyncComfyUIConnection` implementation.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_COMFYUI_URL,
        async_manager: AsyncManager | None = None,
    ):
        super().__init__(
            async_manager=async_manager or AsyncManager(),
            base_url=base_url,
        )

    async def connect_with_retry(
        self,
        max_retries: int = 3,
        delay: float = 0.5,
    ) -> bool:
        """Attempt connection with retry/backoff."""

        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                result = await self.connect()
                if result:
                    return True
            except Exception as exc:  # pragma: no cover - re-raised below
                last_exc = exc

            if attempt < max_retries - 1:
                await asyncio.sleep(delay)

        if last_exc:
            raise last_exc
        return False
