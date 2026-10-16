"""Render detached scene snapshots through wgpu without blocking Qt's event loop."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.ui.canvas.wgpu_backend import WgpuOffscreenRenderer
from loomstroke.ui.canvas.wgpu_backend.types import WgpuBackendError, WgpuUnavailableError

from .editing_common import check_gui_thread, current_revision
from .serialization import detach_scene_snapshot
from .types import McpStructuredError, ReasonCode


def _render(scene: Any, invalidation: Any) -> Any:
    renderer = WgpuOffscreenRenderer()
    try:
        return renderer.render_scene(scene, invalidation=invalidation)
    except WgpuUnavailableError as exc:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "wgpu readback is unavailable."
        ) from exc
    except WgpuBackendError as exc:
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED,
            "wgpu readback failed.",
            details={"errorType": type(exc).__name__},
        ) from exc
    finally:
        renderer.release()


async def composite_image(service: Any, arguments: Mapping[str, Any] | None = None) -> QImage:
    check_gui_thread()
    before = current_revision(service)
    canvas = service.canvas_service
    scene = detach_scene_snapshot(service._resolve_scene_for_arguments(arguments or {}))
    invalidation = (
        service._invalidation_provider() if service._invalidation_provider is not None else None
    )
    result = await asyncio.to_thread(_render, scene, invalidation)
    check_gui_thread()
    if service.canvas_service is not canvas or current_revision(service) != before:
        raise McpStructuredError(ReasonCode.CONFLICT, "The document changed during GPU readback.")
    rgba = result.rgba
    height, width = rgba.shape[:2]
    return QImage(rgba.data, width, height, rgba.strides[0], QImage.Format.Format_RGBA8888).copy()
