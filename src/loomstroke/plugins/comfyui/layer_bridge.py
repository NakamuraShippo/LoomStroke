"""Layer bridge for ComfyUI integration.

This module provides layer export/import functionality through
LayerTransferBridge so that ComfyUI remains compliant with the
plugin architecture.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from loomstroke.host_api import LayerRef
from loomstroke.plugins.common.layer_bridge import (
    LayerSnapshot,
    LayerTransferBridge,
)
from loomstroke.ui.widget_translations import bind_widget_text

if TYPE_CHECKING:
    from loomstroke.host_api import HostAPI

logger = logging.getLogger(__name__)


class LayerBridge:
    """Bridge between ComfyUI and LoomStroke layers using the shared bridge."""

    def __init__(self, host_api: HostAPI):
        self.host_api = host_api
        self._bridge = LayerTransferBridge(host_api)

    async def export_layer(
        self,
        layer_ref: LayerRef | None = None,
        fmt: str = "png",
    ) -> bytes:
        snapshot = await self._capture(layer_ref, fmt=fmt)
        return snapshot.data

    async def export_layer_to_file(
        self,
        layer_ref: LayerRef | None = None,
        fmt: str = "png",
    ) -> Path:
        snapshot = await self._capture(layer_ref, fmt=fmt)
        return snapshot.path

    async def export_layer_with_timestamp(
        self,
        layer_ref: LayerRef | None = None,
        fmt: str = "png",
    ) -> Path:
        # Host-side snapshot already includes timestamped names when requested.
        snapshot = await self._capture(layer_ref, fmt=fmt)
        return snapshot.path

    async def _capture(self, layer_ref: LayerRef | None, *, fmt: str) -> LayerSnapshot:
        try:
            snapshot = await self._bridge.capture_async(layer_ref, fmt=fmt)
            return snapshot
        except Exception as exc:
            logger.error("Failed to capture layer snapshot: %s", exc)
            raise

    async def _create_layer(self, name: str) -> LayerRef:
        try:
            return self._bridge.ensure_layer(None, create_if_missing=True, name=name)
        except Exception as exc:
            logger.error("Failed to create new layer '%s': %s", name, exc)
            raise

    async def import_image_to_layer(
        self,
        image_data: bytes,
        layer_ref: LayerRef,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not image_data:
            raise ValueError("No image data provided for layer import")
        try:
            image_array = await self._bridge.decode_async(image_data)
        except Exception as exc:
            logger.error("Failed to decode generated image: %s", exc)
            raise

        try:
            target_layer = self._bridge.ensure_layer(layer_ref)
        except Exception as exc:
            logger.error("Layer resolution failed: %s", exc)
            raise

        command_metadata: dict[str, Any] = {
            "source": "ComfyUI",
            "target_layer_id": target_layer.id,
            "target_layer_name": target_layer.name,
        }
        if metadata:
            command_metadata.update(metadata)

        try:
            result = await self._bridge.apply_async(
                image_array,
                layer=target_layer,
                metadata=command_metadata,
                merge_key=f"comfyui_output_{target_layer.id}",
            )
        except Exception as exc:
            logger.error("Failed to submit ApplyImageCommand: %s", exc)
            raise

        if not result.command_executed:
            raise RuntimeError("ApplyImageCommand did not execute successfully")

    def cleanup_temp_files(self) -> None:
        removed = self._bridge.cleanup_cache(include_host_cache=True)
        logger.info("Cleaned up %s cached layer files", removed)


class LayerSelector(QWidget):
    """Layer selection widget for ComfyUI operations."""

    layer_selected = Signal(object)  # LayerRef

    def __init__(self, host_api: HostAPI, parent: QWidget | None = None):
        from PySide6.QtWidgets import QComboBox, QLabel, QVBoxLayout  # noqa: PLC0415

        super().__init__(parent)
        self.host_api = host_api

        layout = QVBoxLayout()

        layout.addWidget(bind_widget_text(QLabel(), "Select Layer:", domain="LayerSelector"))

        self.layer_combo = QComboBox()
        self.layer_combo.currentIndexChanged.connect(self.on_selection_changed)
        layout.addWidget(self.layer_combo)

        self.setLayout(layout)

        self.update_layers()

    def update_layers(self) -> None:
        self.layer_combo.clear()
        canvas = self.host_api.canvas
        if canvas:
            layers = canvas.get_layers()
            current = canvas.current_layer()
            for layer in layers:
                self.layer_combo.addItem(layer.name, layer)
                if current and layer.id == current.id:
                    self.layer_combo.setCurrentIndex(self.layer_combo.count() - 1)

    def on_selection_changed(self, index: int) -> None:
        if index >= 0:
            layer = self.layer_combo.itemData(index)
            if layer:
                self.layer_selected.emit(layer)

    def get_selected_layer(self) -> LayerRef | None:
        index = self.layer_combo.currentIndex()
        if index >= 0:
            return self.layer_combo.itemData(index)
        return None
