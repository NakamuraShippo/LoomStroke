"""Undoable replacement of a linked image and record on the original page."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from .command_types import Command
from .commands_layer_lookup import find_layer
from .linked_assets.resource_layer_metadata_codec import ResourceLayerMetadataCodec
from .page_history_memory import estimate_metadata_payload_bytes


def resource_replacement_image(layer: Any, image: QImage) -> QImage:
    """Keep the existing placement and pad a smaller source with transparency."""
    current = getattr(layer, "image", None)
    width = max(image.width(), current.width() if isinstance(current, QImage) else 0)
    height = max(image.height(), current.height() if isinstance(current, QImage) else 0)
    if width == image.width() and height == image.height():
        return image
    expanded = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    if expanded.isNull():
        raise ValueError("Could not prepare resource image")
    expanded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(expanded)
    try:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(0, 0, image)
    finally:
        painter.end()
    return expanded


def refreshable_layer(layer: Any) -> bool:
    return (
        getattr(layer, "layer_type", None) == "raster"
        and getattr(layer, "native_cmyk", None) is None
        and isinstance(getattr(layer, "image", None), QImage)
    )


@dataclass(frozen=True)
class _ImageAndMetadata:
    image: QImage | None
    metadata: dict[str, Any] | None


class _ResourceRefreshCommand(Command):
    def __init__(self, scope, image: QImage | None, record: Any, label: str, *, codec=None) -> None:
        super().__init__()
        self.label = label
        self.scope = scope
        self.changes_pixels = image is not None
        self.layer_ids = tuple(layer.id for layer in scope.layers)
        self.before = tuple(
            _ImageAndMetadata(QImage(layer.image) if self.changes_pixels else None, layer.metadata)
            for layer in scope.layers
        )
        codec = codec or ResourceLayerMetadataCodec()
        sizes: dict[tuple[int, int], QImage] = {}
        prepared = []
        for layer in scope.layers:
            replacement = None
            if image is not None:
                key = (layer.image.width(), layer.image.height())
                if key not in sizes:
                    sizes[key] = resource_replacement_image(layer, image)
                replacement = sizes[key]
            if record is None:
                metadata = codec.strip(layer.metadata)
            else:
                metadata = codec.write(layer.metadata, record)
                metadata[codec.NAMESPACE] = deepcopy(metadata[codec.NAMESPACE])
            prepared.append(_ImageAndMetadata(replacement, metadata))
        self.after = tuple(prepared)
        self.installed: tuple[_ImageAndMetadata, ...] = ()

    @property
    def retained_bytes(self) -> int:
        images, metadata = set(), set()
        total = 0
        for value in (*self.before, *self.after):
            if value.image is not None:
                key = value.image.cacheKey()
                if key not in images:
                    images.add(key)
                    total += value.image.sizeInBytes()
            total += estimate_metadata_payload_bytes(value.metadata, seen=metadata)
        return total

    def _install(self, canvas: Any, values: tuple[_ImageAndMetadata, ...]) -> bool:
        layers = tuple(find_layer(canvas, layer_id) for layer_id in self.layer_ids)
        if not all(refreshable_layer(layer) for layer in layers):
            return False
        # Distinct wrappers preserve Qt copy-on-write between the consumers and
        # history; the pixels themselves are shared until a later edit.
        replacements = tuple(
            _ImageAndMetadata(
                QImage(value.image) if value.image is not None else None, value.metadata
            )
            for value in values
        )
        for layer, value in zip(layers, replacements, strict=True):
            if value.image is not None:
                layer.image = value.image
                layer.texture_version += 1
            layer.metadata = value.metadata
        if self.scope is not None:
            self.installed = replacements
        return True

    def apply(self, canvas: Any) -> bool:
        if self.scope is not None and not self.scope.matches():
            return False
        if not self._install(canvas, self.after):
            return False
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed or not self._install(canvas, self.before):
            return False
        self._executed = False
        return True

    def finish(self) -> None:
        self.scope = None
        self.installed = ()
