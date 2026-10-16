"""Asynchronous layer snapshot queue handling."""

from __future__ import annotations

import asyncio
import io
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loomstroke.host_api import HostAPI, LayerRef

from ..comfyui.layer_bridge import LayerBridge  # type: ignore[import]  # optional cross-plugin
from .errors import AttachmentError
from .types import LayerMetadata, LayerSelectionRequest, LayerSnapshotResult

try:
    from PIL import Image  # type: ignore[import]  # optional dependency
except Exception:  # pragma: no cover - optional dependency
    Image = None  # type: ignore[assignment]  # optional dependency fallback


LayerTaskCallback = Callable[[LayerSnapshotResult | None, Exception | None], Awaitable[None] | None]


@dataclass
class LayerTaskHandle:
    request: LayerSelectionRequest
    callback: LayerTaskCallback


class LayerTaskQueue:
    """Serialize layer snapshot capture requests."""

    def __init__(self, host: HostAPI, *, max_size: int = 4, snapshot_fmt: str = "png") -> None:
        self._host = host
        self._bridge = LayerBridge(host)
        self._snapshot_fmt = snapshot_fmt
        self._queue: asyncio.Queue[LayerTaskHandle | None] = asyncio.Queue()
        self._callbacks: dict[str, LayerTaskCallback] = {}
        self._worker: asyncio.Task[None] | None = None
        self._max_size = max_size
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            loop = asyncio.get_running_loop()
            self._worker = loop.create_task(self._run())

    async def enqueue(self, request: LayerSelectionRequest, callback: LayerTaskCallback) -> str:
        if self._queue.qsize() >= self._max_size:
            raise AttachmentError("layer request queue is full")
        handle = LayerTaskHandle(request=request, callback=callback)
        await self._queue.put(handle)
        self._callbacks[request.request_id] = callback
        return request.request_id

    async def cancel(self, request_id: str) -> None:
        self._callbacks.pop(request_id, None)

    async def shutdown(self) -> None:
        self._stop.set()
        await self._queue.put(None)
        if self._worker:
            await self._worker
            self._worker = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            handle = await self._queue.get()
            if handle is None:
                break
            request = handle.request
            try:
                result = await self._capture(request)
                error: Exception | None = None
            except Exception as exc:
                result = None
                error = AttachmentError(str(exc))
            callback = self._callbacks.pop(request.request_id, None)
            if callback:
                outcome = callback(result, error)
                if asyncio.iscoroutine(outcome):
                    await outcome
        self._bridge.cleanup_temp_files()

    async def _capture(self, request: LayerSelectionRequest) -> LayerSnapshotResult:
        canvas = getattr(self._host, "canvas", None)
        layer_info: dict[str, Any] | None = None
        layer_ref: LayerRef | None = None
        layer_meta: LayerMetadata | None = None
        if canvas:
            if request.layer_id:
                layer_ref = LayerRef(id=request.layer_id, name=None)
            else:
                layer_ref = canvas.current_layer()
            if request.layer_id and hasattr(canvas, "get_layer_info"):
                try:
                    layer_info = canvas.get_layer_info(request.layer_id)
                except Exception:
                    layer_info = None
            if request.include_metadata and layer_info:
                layer_meta = LayerMetadata(
                    id=request.layer_id or (layer_ref.id if layer_ref else ""),
                    name=layer_info.get("name") or (layer_ref.name if layer_ref else "Layer"),
                    visible=bool(layer_info.get("visible", True)),
                    opacity=float(layer_info.get("opacity", 1.0)),
                    blend_mode=str(layer_info.get("blend_mode", "normal")),
                )
        png_bytes = await self._bridge.export_layer(layer_ref, fmt=self._snapshot_fmt)
        width, height = self._probe_dimensions(png_bytes)
        result = LayerSnapshotResult(
            request_id=request.request_id,
            data=png_bytes,
            mime=f"image/{self._snapshot_fmt}",
            width=width,
            height=height,
            layer=layer_meta,
            source="layer",
            created_at=time.time(),
        )
        return result

    @staticmethod
    def _probe_dimensions(data: bytes) -> tuple[int, int]:
        if not Image:
            return 0, 0
        try:
            with Image.open(io.BytesIO(data)) as img:  # type: ignore[name-defined]  # runtime-resolved import
                width, height = img.size
            return int(width), int(height)
        except Exception:
            return 0, 0
