"""Shared layer transfer utilities for plugins.

This module centralises layer export/import behaviours so that
multiple plugins can reuse the same implementation without duplicating
canvas access logic.
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from loomstroke.core.commands import ApplyImageCommand, DirtyRegion
from loomstroke.host_api import HostAPI, LayerRef
from loomstroke.platform.cache_paths import get_layer_cache_dir

if TYPE_CHECKING:
    from numpy.typing import NDArray


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LayerSnapshot:
    """Snapshot data for a captured layer."""

    layer: LayerRef
    path: Path
    data: bytes
    fmt: str


@dataclass(frozen=True)
class LayerTransferResult:
    """Outcome details for an image apply operation."""

    layer: LayerRef
    merge_key: str | None
    metadata: dict[str, Any]
    dirty_region: DirtyRegion | None
    command_executed: bool


class LayerTransferBridge:
    """Common bridge that encapsulates layer capture and apply logic."""

    def __init__(self, host_api: HostAPI, *, temp_dir: Path | None = None) -> None:
        self._host = host_api
        self._cache_dir = get_layer_cache_dir()
        base_temp = temp_dir or (self._cache_dir / "bridge_tmp")
        base_temp.mkdir(parents=True, exist_ok=True)
        self._temp_dir = base_temp
        self._executor: ThreadPoolExecutor | None = None
        self._loop_accessor: Callable[[], asyncio.AbstractEventLoop] = asyncio.get_running_loop

    # ------------------------------------------------------------------
    # Snapshot helpers
    def capture(
        self,
        layer: LayerRef | None = None,
        *,
        fmt: str = "png",
        name: str | None = None,
    ) -> LayerSnapshot:
        target = self._resolve_layer_ref(layer)
        snapshot_path = self._cache_snapshot(target, fmt=fmt, name=name)
        data = snapshot_path.read_bytes()
        logger.debug("Captured layer %s to %s (%d bytes)", target.id, snapshot_path, len(data))
        return LayerSnapshot(layer=target, path=snapshot_path, data=data, fmt=fmt)

    async def capture_async(
        self,
        layer: LayerRef | None = None,
        *,
        fmt: str = "png",
        name: str | None = None,
    ) -> LayerSnapshot:
        # Run on the current (Qt) event loop to avoid touching Qt objects off-thread.
        return self.capture(layer=layer, fmt=fmt, name=name)

    # ------------------------------------------------------------------
    # Decode helpers
    def decode(self, image_bytes: bytes, *, mode: str = "RGBA") -> NDArray[np.uint8]:
        if not image_bytes:
            raise ValueError("image_bytes is empty")
        try:
            from PIL import Image  # type: ignore  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - optional dependency
            logger.error("Pillow is required to decode image bytes")
            raise RuntimeError("Pillow is not available") from exc

        with Image.open(io.BytesIO(image_bytes)) as image:
            if mode and image.mode != mode:
                image = image.convert(mode)  # noqa: PLW2901
            array = np.array(image)
        logger.debug("Decoded image bytes into array with shape %s", array.shape)
        return array

    async def decode_async(self, image_bytes: bytes, *, mode: str = "RGBA") -> NDArray[np.uint8]:
        loop = self._loop_accessor()
        return await loop.run_in_executor(None, lambda: self.decode(image_bytes, mode=mode))

    # ------------------------------------------------------------------
    # Layer management
    def ensure_layer(
        self,
        layer: LayerRef | None,
        *,
        create_if_missing: bool = False,
        name: str | None = None,
    ) -> LayerRef:
        if layer and layer.id:
            return layer
        canvas = getattr(self._host, "canvas", None)
        if not canvas:
            raise RuntimeError("Canvas service unavailable")
        resolved = canvas.current_layer()
        if resolved and resolved.id:
            return resolved
        if create_if_missing and hasattr(canvas, "add_layer"):
            created = canvas.add_layer(name or "Layer")
            if isinstance(created, LayerRef):
                return created
            if isinstance(created, dict):
                created_id = created.get("id")
                created_name = created.get("name")
                if created_id:
                    return LayerRef(
                        id=str(created_id), name=str(created_name) if created_name else None
                    )
        raise ValueError("No layer available and cannot create a new layer")

    # ------------------------------------------------------------------
    # Apply helpers
    def apply(
        self,
        image: NDArray[np.uint8],
        *,
        layer: LayerRef,
        position: tuple[int, int] = (0, 0),
        metadata: dict[str, Any] | None = None,
        merge_key: str | None = None,
    ) -> bool:
        result = self._apply_impl(
            image=image,
            layer=layer,
            position=position,
            metadata=metadata,
            merge_key=merge_key,
        )
        return result.command_executed

    async def apply_async(
        self,
        image: NDArray[np.uint8],
        *,
        layer: LayerRef,
        position: tuple[int, int] = (0, 0),
        metadata: dict[str, Any] | None = None,
        merge_key: str | None = None,
    ) -> LayerTransferResult:
        # Apply on the Qt main thread to respect QObject affinity.
        return self._apply_impl(
            image=image,
            layer=layer,
            position=position,
            metadata=metadata,
            merge_key=merge_key,
        )

    def _apply_impl(
        self,
        *,
        image: NDArray[np.uint8],
        layer: LayerRef,
        position: tuple[int, int],
        metadata: dict[str, Any] | None,
        merge_key: str | None,
    ) -> LayerTransferResult:
        canvas = getattr(self._host, "canvas", None)
        if not canvas or not hasattr(canvas, "submit"):
            raise RuntimeError("Canvas service unavailable")
        payload = dict(metadata or {})
        payload.setdefault("target_layer_id", layer.id)
        payload.setdefault("target_layer_name", layer.name)
        command = ApplyImageCommand(
            image=np.array(image),
            position=position,
            metadata=payload,
            merge_key=merge_key,
        )
        executed = False
        try:
            executed = bool(canvas.submit(command, merge_key=merge_key))
        except Exception as exc:
            logger.warning("canvas.submit failed for layer %s: %s", layer.id, exc)
        if not executed:
            try:
                executed = command.apply(canvas)
            except Exception as exc:
                logger.error("Failed to apply command directly: %s", exc)
                executed = False
        dirty = command.dirty_region
        logger.info(
            "Applied image to layer %s (executed=%s, dirty=%s)",
            layer.id,
            executed,
            dirty,
        )
        return LayerTransferResult(
            layer=layer,
            merge_key=merge_key,
            metadata=payload,
            dirty_region=dirty,
            command_executed=executed,
        )

    # ------------------------------------------------------------------
    def cleanup_cache(self, *, include_host_cache: bool = False) -> int:
        removed = 0
        for file in self._temp_dir.glob("*.png"):
            try:
                file.unlink()
                removed += 1
            except FileNotFoundError:
                continue
        if include_host_cache:
            for file in self._cache_dir.glob("*.png"):
                try:
                    file.unlink()
                    removed += 1
                except FileNotFoundError:
                    continue
        logger.debug("Removed %d cached layer files", removed)
        return removed

    def close(self) -> None:
        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor = None

    # ------------------------------------------------------------------
    def _resolve_layer_ref(self, layer: LayerRef | None) -> LayerRef:
        if layer and layer.id:
            return layer
        canvas = getattr(self._host, "canvas", None)
        if not canvas:
            raise RuntimeError("Canvas service unavailable")
        current = canvas.current_layer()
        if not current or not current.id:
            raise ValueError("No layer available for capture")
        return current

    def _cache_snapshot(self, layer: LayerRef, *, fmt: str, name: str | None) -> Path:
        canvas = getattr(self._host, "canvas", None)
        if not canvas or not hasattr(canvas, "cache_layer_snapshot"):
            raise RuntimeError("Layer snapshot API unavailable")
        snapshot = canvas.cache_layer_snapshot(
            layer_id=layer.id,
            fmt=fmt,
            name=name or layer.name or layer.id,
        )
        return Path(snapshot)


__all__ = [
    "LayerSnapshot",
    "LayerTransferBridge",
    "LayerTransferResult",
]
