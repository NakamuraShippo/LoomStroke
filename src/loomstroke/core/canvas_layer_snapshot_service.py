"""Layer snapshot cache helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.core.appearance.input_styles import render_input_styles
from loomstroke.core.assets_3d.scene import Scene3DDocument
from loomstroke.core.document_capture_guard import assert_document_capture_ready
from loomstroke.platform.cache_paths import build_snapshot_path, normalize_image_format

logger = logging.getLogger("loomstroke.core.canvas_service_simple")


class CanvasLayerSnapshotService:
    """Persist layer snapshots while leaving canvas state mutation to the owner."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def cache_layer_snapshot(
        self, layer_id: str | None = None, fmt: str = "png", name: str | None = None
    ) -> str:
        target_layer = self._snapshot_layer(layer_id)
        snapshot_image = self._snapshot_image_for_layer(target_layer)
        normalized_fmt = self._snapshot_format(fmt)
        stem = name or target_layer.name or target_layer.id
        return self._persist_snapshot(
            layer_id=target_layer.id,
            layer_name=target_layer.name,
            snapshot_image=snapshot_image,
            fmt=normalized_fmt,
            stem=stem,
        )

    def _snapshot_layer(self, layer_id: str | None) -> Any:
        if layer_id is None:
            if self._owner._active_index >= len(self._owner._layers):
                raise ValueError("Active layer out of range")
            # By id, so a nested active layer is snapshotted rather than the
            # ancestor group its root index points at.
            return self._owner._get_active_layer()

        target_layer = self._owner._get_layer_by_id(layer_id)
        if target_layer is None:
            raise ValueError(f"Layer not found: {layer_id}")
        return target_layer

    def _snapshot_image_for_layer(self, target_layer: Any) -> QImage:
        image_copy: QImage | None
        if target_layer.layer_type == "vector":
            image_copy = self._owner._rasterize_vector_layer(target_layer)
        elif target_layer.layer_type == "3d":
            image_copy = self.rasterize_3d_layer(target_layer)
        else:
            image_copy = target_layer.image.copy() if target_layer.image else None
        if image_copy is None or image_copy.isNull():
            raise ValueError(f"Layer has no image buffer: {target_layer.name}")
        image_copy = render_input_styles(image_copy, target_layer)
        if image_copy.format() == QImage.Format.Format_ARGB32_Premultiplied:
            return image_copy.convertToFormat(QImage.Format.Format_ARGB32)
        return image_copy

    def rasterize_3d_layer(self, layer: Any) -> QImage:
        """Capture an isolated 3D view through a UI-owned rendering port."""
        assert_document_capture_ready(self._owner)
        if not isinstance(getattr(layer, "scene_3d", None), Scene3DDocument):
            raise ValueError("3D input layer has no renderable scene")
        renderer = getattr(self._owner, "_three_d_snapshot_renderer", None)
        if not callable(renderer):
            raise RuntimeError("3D input snapshot renderer is unavailable")
        width, height = self._owner.get_canvas_size()
        image = renderer(layer, width, height)
        if (
            not isinstance(image, QImage)
            or image.isNull()
            or image.width() != width
            or image.height() != height
        ):
            raise ValueError("3D input snapshot renderer returned an invalid image")
        return image

    @staticmethod
    def _snapshot_format(fmt: str) -> str:
        normalized_fmt = normalize_image_format(fmt)
        if normalized_fmt != (fmt or "").lower():
            logger.debug("Falling back to supported format '%s' for layer snapshot", normalized_fmt)
        return normalized_fmt

    @staticmethod
    def _snapshot_path(stem: str, fmt: str) -> Path:
        return build_snapshot_path(stem, fmt)

    def _persist_snapshot(
        self,
        *,
        layer_id: str,
        layer_name: str,
        snapshot_image: QImage,
        fmt: str,
        stem: str,
    ) -> str:
        snapshot_path = self._snapshot_path(stem, fmt)
        temp_path = snapshot_path.with_suffix(snapshot_path.suffix + ".tmp")
        if temp_path.exists():
            with suppress(OSError):
                temp_path.unlink()

        try:
            # PySide6 accepts a string format name at runtime; the stub only lists bytes-like types.
            if not snapshot_image.save(str(temp_path), fmt.upper()):  # type: ignore[call-overload]
                raise OSError(f"Failed to save layer snapshot for {layer_name}")
            temp_path.replace(snapshot_path)
        except Exception:
            with suppress(FileNotFoundError):
                temp_path.unlink()
            raise

        logger.info("Cached layer snapshot %s -> %s", layer_id, snapshot_path)
        return str(snapshot_path)
